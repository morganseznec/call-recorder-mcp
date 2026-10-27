// CallRecorder: records the other side of a call (system or per-app audio, via a
// Core Audio process tap) and the local microphone into two aligned mono WAV files.
//
//   CallRecorder list
//   CallRecorder record --out DIR [--bundles a,b,c] [--no-mic] [--max-minutes N]
//
// `record` is meant to be launched through `open` so that macOS treats the app as
// its own responsible process for the microphone and audio capture permissions.
// It stops cleanly on SIGINT or SIGTERM.

import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

// MARK: - Arguments and logging

let args = CommandLine.arguments
func option(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}
func flag(_ name: String) -> Bool { args.contains(name) }

var logHandle: FileHandle?
func log(_ message: String) {
    let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(message)\n"
    (logHandle ?? FileHandle.standardError).write(line.data(using: .utf8)!)
}

func writeJSON(_ object: Any, to url: URL) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]) else { return }
    let tmp = url.appendingPathExtension("tmp")
    try? data.write(to: tmp)
    _ = try? FileManager.default.replaceItemAt(url, withItemAt: tmp)
}

// MARK: - Core Audio property helpers

let systemObject = AudioObjectID(kAudioObjectSystemObject)

func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
}

func getValue<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ fallback: T) -> T {
    var addr = address(selector)
    var size = UInt32(MemoryLayout<T>.size)
    var value = fallback
    let status = withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(object, &addr, 0, nil, &size, $0) }
    return status == noErr ? value : fallback
}

func getString(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
    var addr = address(selector)
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    var value: Unmanaged<CFString>?
    guard AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &value) == noErr, let value else { return nil }
    return value.takeRetainedValue() as String
}

func getObjectList(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> [AudioObjectID] {
    var addr = address(selector)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(object, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

struct AudioProcess {
    let id: AudioObjectID
    let pid: pid_t
    let bundleID: String
    let name: String
    let playing: Bool
}

func processName(_ pid: pid_t) -> String {
    var buffer = [CChar](repeating: 0, count: 1024)
    proc_name(pid, &buffer, UInt32(buffer.count))
    return String(cString: buffer)
}

func audioProcesses() -> [AudioProcess] {
    getObjectList(systemObject, kAudioHardwarePropertyProcessObjectList).map { id in
        let pid: pid_t = getValue(id, kAudioProcessPropertyPID, -1)
        return AudioProcess(
            id: id,
            pid: pid,
            bundleID: getString(id, kAudioProcessPropertyBundleID) ?? "",
            name: processName(pid),
            playing: getValue(id, kAudioProcessPropertyIsRunningOutput, UInt32(0)) != 0
        )
    }
}

func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioDeviceID {
    getValue(systemObject, selector, AudioDeviceID(kAudioObjectUnknown))
}

// MARK: - Track writer

/// Writes one mono 48 kHz track. Gaps in the incoming stream (tap rebuilt, device
/// switched, app not started yet) are filled with silence from the host clock, so
/// the mic and remote tracks stay aligned on the same timeline.
final class TrackWriter {
    static let sampleRate = 48_000.0
    let file: AVAudioFile
    let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false)!
    let startHostTime: UInt64
    private var converter: AVAudioConverter?
    private var converterInput: AVAudioFormat?
    private(set) var framesWritten: AVAudioFramePosition = 0
    private var peak: Float = 0
    private let lock = NSLock()

    init(url: URL, startHostTime: UInt64) throws {
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: Self.sampleRate,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        file = try AVAudioFile(forWriting: url, settings: settings, commonFormat: .pcmFormatFloat32, interleaved: false)
        self.startHostTime = startHostTime
    }

    private func expectedFrames(at hostTime: UInt64) -> AVAudioFramePosition {
        guard hostTime > startHostTime else { return 0 }
        return AVAudioFramePosition(AVAudioTime.seconds(forHostTime: hostTime - startHostTime) * Self.sampleRate)
    }

    private func writeSilence(_ frames: AVAudioFramePosition) {
        var remaining = frames
        while remaining > 0 {
            let chunk = AVAudioFrameCount(min(remaining, 48_000))
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: chunk)!
            buffer.frameLength = chunk  // freshly allocated buffers are zeroed
            try? file.write(from: buffer)
            framesWritten += AVAudioFramePosition(chunk)
            remaining -= AVAudioFramePosition(chunk)
        }
    }

    func append(_ input: AVAudioPCMBuffer, hostTime: UInt64) {
        lock.lock(); defer { lock.unlock() }
        let gap = expectedFrames(at: hostTime) - framesWritten
        if gap > 9_600 { writeSilence(gap) }  // more than 200 ms behind the clock

        if converterInput != input.format {
            converter = AVAudioConverter(from: input.format, to: format)
            converter?.downmix = true
            converterInput = input.format
        }
        guard let converter else { return }
        let capacity = AVAudioFrameCount(Double(input.frameLength) * Self.sampleRate / input.format.sampleRate) + 256
        let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity)!
        var consumed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if consumed { status.pointee = .noDataNow; return nil }
            consumed = true
            status.pointee = .haveData
            return input
        }
        guard output.frameLength > 0 else { return }
        if let samples = output.floatChannelData?[0] {
            for i in 0..<Int(output.frameLength) { peak = max(peak, abs(samples[i])) }
        }
        try? file.write(from: output)
        framesWritten += AVAudioFramePosition(output.frameLength)
    }

    /// Peak level since the last call, in dBFS.
    func takePeak() -> Double {
        lock.lock(); defer { lock.unlock() }
        let value = peak
        peak = 0
        return value > 0 ? (20 * log10(Double(value))).rounded() : -120
    }

    func finish(at hostTime: UInt64) {
        lock.lock(); defer { lock.unlock() }
        let gap = expectedFrames(at: hostTime) - framesWritten
        if gap > 0 { writeSilence(gap) }
        // Before macOS 15 the file is closed when the writer is released (the process exits right after).
        if #available(macOS 15, *) { file.close() }
    }
}

// MARK: - Remote audio (process tap)

/// Captures what other apps play. With `bundlePrefixes` empty it taps the whole
/// system output; otherwise only processes whose bundle ID starts with one of the
/// prefixes. The tap is rebuilt whenever the matching processes or the default
/// output device change (an app joining the call late, AirPods connecting...).
final class TapRecorder {
    let writer: TrackWriter
    let bundlePrefixes: [String]
    private let queue = DispatchQueue(label: "call-recorder.tap", qos: .userInteractive)
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var procID: AudioDeviceIOProcID?
    private var currentKey: String?
    private(set) var matched: [String] = []

    init(writer: TrackWriter, bundlePrefixes: [String]) {
        self.writer = writer
        self.bundlePrefixes = bundlePrefixes
    }

    private func targets() -> [AudioProcess]? {
        guard !bundlePrefixes.isEmpty else { return nil }
        return audioProcesses().filter { p in bundlePrefixes.contains { p.bundleID.hasPrefix($0) } }
    }

    /// Called at start and then periodically.
    func refresh() {
        let processes = targets()
        let output = defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice)
        let key = "\(output)|" + (processes?.map { String($0.id) }.sorted().joined(separator: ",") ?? "*")
        guard key != currentKey else { return }
        currentKey = key
        teardown()
        matched = processes?.map { "\($0.bundleID) (\($0.pid))" } ?? ["*"]
        if let processes, processes.isEmpty {
            log("tap: no matching process yet for \(bundlePrefixes), waiting")
            return
        }
        do {
            try build(processes: processes?.map(\.id), outputDevice: output)
            log("tap: capturing \(matched.joined(separator: ", "))")
        } catch {
            log("tap: \(error)")
            currentKey = nil  // retry on next refresh
        }
    }

    private func build(processes: [AudioObjectID]?, outputDevice: AudioDeviceID) throws {
        let description = processes.map { CATapDescription(stereoMixdownOfProcesses: $0) }
            ?? CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.uuid = UUID()
        description.name = "call-recorder"
        description.isPrivate = true
        description.muteBehavior = .unmuted

        var status = AudioHardwareCreateProcessTap(description, &tapID)
        guard status == noErr else { throw NSError(domain: "AudioHardwareCreateProcessTap", code: Int(status)) }

        var streamDescription: AudioStreamBasicDescription = getValue(tapID, kAudioTapPropertyFormat, AudioStreamBasicDescription())
        guard let format = AVAudioFormat(streamDescription: &streamDescription) else {
            throw NSError(domain: "tap format", code: 0)
        }

        let outputUID = getString(outputDevice, kAudioDevicePropertyDeviceUID) ?? ""
        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "call-recorder",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
            kAudioAggregateDeviceTapListKey: [[
                kAudioSubTapDriftCompensationKey: true,
                kAudioSubTapUIDKey: description.uuid.uuidString,
            ]],
        ]
        status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
        guard status == noErr else { throw NSError(domain: "AudioHardwareCreateAggregateDevice", code: Int(status)) }

        // The tap's stream comes after any input stream of the sub-device, so take
        // the last buffers of the list.
        let buffersPerFrame = format.isInterleaved ? 1 : Int(format.channelCount)
        let bytesPerFrame = Int(streamDescription.mBytesPerFrame)
        let writer = self.writer
        status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) { _, inputData, inputTime, _, _ in
            let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inputData))
            guard list.count >= buffersPerFrame, bytesPerFrame > 0 else { return }
            let first = list.count - buffersPerFrame
            let frames = AVAudioFrameCount(Int(list[first].mDataByteSize) / bytesPerFrame)
            guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { return }
            buffer.frameLength = frames
            let destination = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
            for i in 0..<buffersPerFrame {
                let source = list[first + i]
                guard let from = source.mData, let to = destination[i].mData else { continue }
                memcpy(to, from, Int(min(source.mDataByteSize, destination[i].mDataByteSize)))
            }
            writer.append(buffer, hostTime: inputTime.pointee.mHostTime)
        }
        guard status == noErr else { throw NSError(domain: "AudioDeviceCreateIOProcIDWithBlock", code: Int(status)) }
        status = AudioDeviceStart(aggregateID, procID)
        guard status == noErr else { throw NSError(domain: "AudioDeviceStart", code: Int(status)) }
    }

    func teardown() {
        if aggregateID != kAudioObjectUnknown {
            AudioDeviceStop(aggregateID, procID)
            if let procID { AudioDeviceDestroyIOProcID(aggregateID, procID) }
            AudioHardwareDestroyAggregateDevice(aggregateID)
        }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
        tapID = AudioObjectID(kAudioObjectUnknown)
        aggregateID = AudioObjectID(kAudioObjectUnknown)
        procID = nil
    }
}

// MARK: - Microphone

final class MicRecorder {
    let writer: TrackWriter
    private var engine = AVAudioEngine()
    private var observer: NSObjectProtocol?
    private(set) var deviceName = ""

    init(writer: TrackWriter) { self.writer = writer }

    func start() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw NSError(domain: "microphone unavailable", code: 0)
        }
        let writer = self.writer
        // Deprecated in macOS 27 for a throwing variant that Swift only exposes as
        // `__installTap(...error:)` and that needs macOS 27: kept for macOS 14.4+.
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, time in
            writer.append(buffer, hostTime: time.isHostTimeValid ? time.hostTime : mach_absolute_time())
        }
        try engine.start()
        deviceName = getString(defaultDevice(kAudioHardwarePropertyDefaultInputDevice), kAudioObjectPropertyName) ?? "?"
        log("mic: \(deviceName), \(format)")

        // Switching input device (AirPods, iPhone mic) stops the engine: start again.
        observer = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            log("mic: configuration changed, restarting")
            self.stop()
            self.engine = AVAudioEngine()
            do { try self.start() } catch { log("mic: restart failed: \(error)") }
        }
    }

    func stop() {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }
}

// MARK: - Recording

/// One recording: both tracks, the status file (capture.json) and the periodic refresh.
final class Recording {
    let directory: URL
    let bundles: [String]
    let useMic: Bool
    let maxDuration: TimeInterval
    private let statusURL: URL
    private var state: [String: Any]
    private var startDate: Date?
    private var remoteWriter: TrackWriter?
    private var tap: TapRecorder?
    private var mic: MicRecorder?
    private var timer: DispatchSourceTimer?

    init(directory: URL, bundles: [String], useMic: Bool, maxDuration: TimeInterval) {
        self.directory = directory
        self.bundles = bundles
        self.useMic = useMic
        self.maxDuration = maxDuration
        statusURL = directory.appendingPathComponent("capture.json")
        state = ["pid": Int(getpid()), "state": "starting", "started_at": ISO8601DateFormatter().string(from: Date())]
        publish()
    }

    private func publish() { writeJSON(state, to: statusURL) }

    private var elapsed: TimeInterval { startDate.map { Date().timeIntervalSince($0).rounded() } ?? 0 }

    /// Asks for the microphone without blocking, so a stop while the permission
    /// prompt is still open is handled, then starts both tracks.
    func start() {
        guard useMic else { return begin(micGranted: false) }
        state["state"] = "waiting_mic_permission"
        publish()
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async { self.begin(micGranted: granted) }
        }
    }

    private func begin(micGranted: Bool) {
        if useMic {
            state["mic_permission"] = micGranted
            if !micGranted { log("mic: permission denied") }
        }
        let startHostTime = mach_absolute_time()
        startDate = Date()
        do {
            let writer = try TrackWriter(url: directory.appendingPathComponent("remote.wav"), startHostTime: startHostTime)
            remoteWriter = writer
            tap = TapRecorder(writer: writer, bundlePrefixes: bundles)
            tap?.refresh()
        } catch {
            log("cannot create remote.wav: \(error)")
            state["state"] = "error"
            state["error"] = "\(error)"
            publish()
            exit(1)
        }
        if micGranted, let writer = try? TrackWriter(url: directory.appendingPathComponent("mic.wav"), startHostTime: startHostTime) {
            let recorder = MicRecorder(writer: writer)
            do { try recorder.start(); mic = recorder } catch { log("mic: \(error)") }
        }

        state["state"] = "recording"
        state["mic"] = mic != nil
        publish()
        log("recording to \(directory.path)")

        // Every 2 s: follow app/device changes and publish levels so a silent track
        // (missing permission, wrong source) is visible while recording.
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 2, repeating: 2)
        timer.setEventHandler { [weak self] in self?.refresh() }
        timer.resume()
        self.timer = timer
    }

    private func refresh() {
        tap?.refresh()
        state["remote_peak_db"] = remoteWriter?.takePeak()
        state["remote_sources"] = tap?.matched
        if let mic {
            state["mic_peak_db"] = mic.writer.takePeak()
            state["mic_device"] = mic.deviceName
        }
        state["elapsed_seconds"] = elapsed
        publish()
        if elapsed > maxDuration { stop("max_duration") }
    }

    func stop(_ reason: String) -> Never {
        log("stopping: \(reason)")
        timer?.cancel()
        let now = mach_absolute_time()
        tap?.teardown()
        mic?.stop()
        remoteWriter?.finish(at: now)
        mic?.writer.finish(at: now)
        state["state"] = "stopped"
        state["stop_reason"] = reason
        state["duration_seconds"] = elapsed
        publish()
        exit(0)
    }
}

// MARK: - Commands

func listCommand() {
    let processes = audioProcesses()
        .filter { $0.pid != getpid() }
        .map { ["pid": Int($0.pid), "bundle_id": $0.bundleID, "name": $0.name, "playing": $0.playing] as [String: Any] }
    let result: [String: Any] = [
        "processes": processes,
        "default_input": getString(defaultDevice(kAudioHardwarePropertyDefaultInputDevice), kAudioObjectPropertyName) ?? "",
        "default_output": getString(defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice), kAudioObjectPropertyName) ?? "",
    ]
    let data = try! JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

func recordCommand() {
    guard let out = option("--out") else {
        FileHandle.standardError.write("--out DIR is required\n".data(using: .utf8)!)
        exit(2)
    }
    let directory = URL(fileURLWithPath: out, isDirectory: true)
    try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let logURL = directory.appendingPathComponent("capture.log")
    FileManager.default.createFile(atPath: logURL.path, contents: nil)
    logHandle = try? FileHandle(forWritingTo: logURL)
    logHandle?.seekToEndOfFile()

    let recording = Recording(
        directory: directory,
        bundles: (option("--bundles") ?? "").split(separator: ",").map(String.init).filter { !$0.isEmpty },
        useMic: !flag("--no-mic"),
        maxDuration: (Double(option("--max-minutes") ?? "") ?? 240) * 60
    )

    // Installed before anything else so a stop is always handled cleanly.
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let signals = [SIGINT, SIGTERM].map { number -> DispatchSourceSignal in
        let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
        source.setEventHandler { recording.stop(number == SIGINT ? "stop" : "terminated") }
        source.resume()
        return source
    }
    recording.start()
    withExtendedLifetime(signals) { dispatchMain() }
}

switch args.dropFirst().first {
case "list": listCommand()
case "record": recordCommand()
default:
    FileHandle.standardError.write("usage: CallRecorder list | record --out DIR [--bundles a,b] [--no-mic] [--max-minutes N]\n".data(using: .utf8)!)
    exit(2)
}

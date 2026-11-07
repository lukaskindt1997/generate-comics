// Milestone B2: raw OCR with Apple Vision (local, built into macOS).
//
// Tries all four 90° orientations, keeps the one with the most confident text,
// and writes every recognised line (text, confidence, box, angle) as JSON.
// No filtering here: that happens in a later Python step.
//
// Usage:  swift scripts/ocr_vision.swift <image> <output.json> [--no-correction]

import Foundation
import Vision
import ImageIO
import CoreGraphics

struct Line: Codable {
    let text: String
    let confidence: Float
    let alternatives: [String]
    // Normalised coordinates in the upright image, origin top-left, 0...1.
    let x: Double, y: Double, w: Double, h: Double
    let angleDegrees: Double
    // Midpoint of the line's left edge (same coordinate system). Better for
    // reading order and indent detection than the box, on tilted pages.
    let leftX: Double, leftY: Double
}

struct Result: Codable {
    let file: String
    let imageWidth: Int
    let imageHeight: Int
    let orientation: String
    let orientationScores: [String: Double]
    let languageCorrection: Bool
    let engine: String
    let lines: [Line]
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: ocr_vision.swift <image> <output.json> [--no-correction]\n".data(using: .utf8)!)
    exit(2)
}
let inURL = URL(fileURLWithPath: args[1])
let outURL = URL(fileURLWithPath: args[2])
let useCorrection = !args.contains("--no-correction")

guard let src = CGImageSourceCreateWithURL(inURL as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    FileHandle.standardError.write("cannot read image \(inURL.path)\n".data(using: .utf8)!)
    exit(1)
}

func recognise(_ orientation: CGImagePropertyOrientation) throws -> [VNRecognizedTextObservation] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["en-GB", "en-US"]
    req.usesLanguageCorrection = useCorrection
    let handler = VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:])
    try handler.perform([req])
    return req.results ?? []
}

let candidates: [(String, CGImagePropertyOrientation)] =
    [("up", .up), ("right", .right), ("down", .down), ("left", .left)]

var best: (String, [VNRecognizedTextObservation])? = nil
var bestScore = -1.0
var scores: [String: Double] = [:]
for (name, o) in candidates {
    let obs = try recognise(o)
    // Score: confident characters on roughly horizontal lines. Vision reads rotated
    // text in any orientation, so we only count lines that come out upright.
    let score = obs.reduce(0.0) { acc, ob in
        guard let t = ob.topCandidates(1).first else { return acc }
        let angle = atan2(Double(ob.topRight.y - ob.topLeft.y), Double(ob.topRight.x - ob.topLeft.x)) * 180 / .pi
        guard abs(angle) < 20 else { return acc }
        return acc + Double(t.confidence) * Double(t.string.count)
    }
    scores[name] = (score * 10).rounded() / 10
    if score > bestScore { bestScore = score; best = (name, obs) }
}

let (orientName, observations) = best!
var lines: [Line] = []
for ob in observations {
    let cands = ob.topCandidates(3)
    guard let top = cands.first else { continue }
    let bb = ob.boundingBox  // Vision: origin bottom-left
    let dx = Double(ob.topRight.x - ob.topLeft.x)
    let dy = Double(ob.topRight.y - ob.topLeft.y)
    lines.append(Line(
        text: top.string,
        confidence: top.confidence,
        alternatives: cands.dropFirst().map { $0.string },
        x: Double(bb.minX), y: Double(1 - bb.maxY), w: Double(bb.width), h: Double(bb.height),
        angleDegrees: (atan2(dy, dx) * 180 / .pi * 100).rounded() / 100,
        leftX: Double(ob.topLeft.x + ob.bottomLeft.x) / 2,
        leftY: 1 - Double(ob.topLeft.y + ob.bottomLeft.y) / 2
    ))
}

let rotated = orientName == "left" || orientName == "right"
let result = Result(
    file: inURL.lastPathComponent,
    imageWidth: rotated ? image.height : image.width,
    imageHeight: rotated ? image.width : image.height,
    orientation: orientName,
    orientationScores: scores,
    languageCorrection: useCorrection,
    engine: "Apple Vision VNRecognizeTextRequest (accurate)",
    lines: lines
)

try FileManager.default.createDirectory(at: outURL.deletingLastPathComponent(), withIntermediateDirectories: true)
let enc = JSONEncoder()
enc.outputFormatting = [.prettyPrinted, .sortedKeys]
try enc.encode(result).write(to: outURL)
print("\(inURL.lastPathComponent): orientation=\(orientName) lines=\(lines.count) scores=\(scores)")

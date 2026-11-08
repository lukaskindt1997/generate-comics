// Compose several reference images side by side into ONE image of an exact size.
//
// Draw Things (HTTP API, FLUX.2 Klein edit mode) uses a single canvas image as the
// reference. To give a panel several references (a character + an object), we place
// them next to each other on a neutral background, at the panel's output size.
//
// Usage: swift scripts/compose_refs.swift <out.png> <width> <height> <img1> [img2 ...] [--vertical]
//        --vertical stacks the images top to bottom instead of left to right.

import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
guard args.count >= 5, let W = Int(args[2]), let H = Int(args[3]) else {
    FileHandle.standardError.write("usage: compose_refs.swift <out.png> <width> <height> <img1> [img2 ...]\n".data(using: .utf8)!)
    exit(2)
}
let outURL = URL(fileURLWithPath: args[1])
let vertical = args.contains("--vertical")
let inputs = Array(args[4...]).filter { $0 != "--vertical" }

func load(_ path: String) -> CGImage {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        FileHandle.standardError.write("cannot read \(path)\n".data(using: .utf8)!)
        exit(1)
    }
    return img
}

let cs = CGColorSpaceCreateDeviceRGB()
guard let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0,
                          space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(1) }
ctx.setFillColor(CGColor(red: 0.82, green: 0.82, blue: 0.80, alpha: 1))  // neutral light grey
ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
ctx.interpolationQuality = .high

// Equal cells, left to right (or top to bottom); each image scaled to fit, centred.
let n = inputs.count
let gap = 16.0
let cellW = vertical ? Double(W) - 2 * gap : (Double(W) - gap * Double(n + 1)) / Double(n)
let cellH = vertical ? (Double(H) - gap * Double(n + 1)) / Double(n) : Double(H) - 2 * gap
for (i, path) in inputs.enumerated() {
    let img = load(path)
    let scale = min(cellW / Double(img.width), cellH / Double(img.height))
    let w = Double(img.width) * scale, h = Double(img.height) * scale
    var x = (Double(W) - w) / 2, y = (Double(H) - h) / 2
    if vertical {
        // CoreGraphics origin is bottom-left: first image goes on top.
        y = Double(H) - gap - Double(i + 1) * cellH - Double(i) * gap + (cellH - h) / 2
    } else {
        x = gap + Double(i) * (cellW + gap) + (cellW - w) / 2
    }
    ctx.draw(img, in: CGRect(x: x, y: y, width: w, height: h))
}

guard let out = ctx.makeImage(),
      let dest = CGImageDestinationCreateWithURL(outURL as CFURL, UTType.png.identifier as CFString, 1, nil) else { exit(1) }
CGImageDestinationAddImage(dest, out, nil)
CGImageDestinationFinalize(dest)
print("composed \(n) image(s) -> \(outURL.path) (\(W)x\(H))")

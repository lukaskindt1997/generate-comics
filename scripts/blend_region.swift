// Local edit without API mask support: take the ORIGINAL image and paste in only a
// rectangle of the EDITED image, with a soft (feathered) edge. Pixels outside the
// rectangle stay exactly as in the original.
//
// Usage: swift scripts/blend_region.swift <original.png> <edited.png> <out.png> x0 y0 x1 y1 [feather_px]
//        (rectangle in pixels, origin top-left; both images must have the same size)

import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let a = CommandLine.arguments
guard a.count >= 8, let x0 = Int(a[4]), let y0 = Int(a[5]), let x1 = Int(a[6]), let y1 = Int(a[7]) else {
    FileHandle.standardError.write("usage: blend_region.swift <original> <edited> <out> x0 y0 x1 y1 [feather]\n".data(using: .utf8)!)
    exit(2)
}
let feather = a.count > 8 ? Double(a[8]) ?? 24 : 24

func load(_ p: String) -> CGImage {
    guard let s = CGImageSourceCreateWithURL(URL(fileURLWithPath: p) as CFURL, nil),
          let i = CGImageSourceCreateImageAtIndex(s, 0, nil) else { exit(1) }
    return i
}
func rgba(_ img: CGImage) -> [UInt8] {
    var buf = [UInt8](repeating: 0, count: img.width * img.height * 4)
    let ctx = CGContext(data: &buf, width: img.width, height: img.height, bitsPerComponent: 8,
                        bytesPerRow: img.width * 4, space: CGColorSpaceCreateDeviceRGB(),
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(img, in: CGRect(x: 0, y: 0, width: img.width, height: img.height))
    return buf
}

let orig = load(a[1]), edit = load(a[2])
guard orig.width == edit.width, orig.height == edit.height else {
    FileHandle.standardError.write("images differ in size\n".data(using: .utf8)!); exit(1)
}
let W = orig.width, H = orig.height
var po = rgba(orig)
let pe = rgba(edit)
// Buffer rows are top-down here (CGContext with data pointer stores row 0 = top).
for y in 0..<H {
    for x in 0..<W {
        // distance inside the rectangle, 0 at the edge -> weight ramps up over `feather` px
        let dx = min(Double(x - x0), Double(x1 - 1 - x))
        let dy = min(Double(y - y0), Double(y1 - 1 - y))
        let d = min(dx, dy)
        if d < 0 { continue }
        let w = min(1.0, (d + 1) / feather)
        let i = (y * W + x) * 4
        for k in 0..<3 {
            po[i + k] = UInt8(Double(po[i + k]) * (1 - w) + Double(pe[i + k]) * w)
        }
    }
}
let ctx = CGContext(data: &po, width: W, height: H, bitsPerComponent: 8, bytesPerRow: W * 4,
                    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let out = ctx.makeImage()!
let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: a[3]) as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, out, nil)
CGImageDestinationFinalize(dest)
print("blended region (\(x0),\(y0))-(\(x1),\(y1)) feather \(Int(feather))px -> \(a[3])")

// Render comic pages to a PDF (one PDF page per comic page), using only macOS frameworks.
//
// Input: a JSON spec (written by render_pages.py) with page size in pixels at 300 dpi, the
// lettering settings, and per page the panels: box, image (or null), texts (captions and
// dialogue with placement). Images are cover-fitted into their box; text boxes are drawn
// with CoreText (real, selectable text) at the same relative size as the UI preview.
//
// Usage: swift scripts/render_pages.swift <spec.json> <out.pdf>

import Foundation
import CoreGraphics
import CoreText
import ImageIO

struct Lettering: Decodable { let font_px: Double; let line_height: Double; let padding_px: Double
                              let max_width_px: Double; let margin_px: Double }
struct Box: Decodable { let x: Double; let y: Double; let w: Double; let h: Double }
struct TextItem: Decodable { let kind: String; let text: String; let placement: String }
struct Panel: Decodable { let panel: Int; let box: Box; let image: String?; let texts: [TextItem] }
struct Page: Decodable { let page: Int; let panels: [Panel] }
struct Spec: Decodable { let width: Double; let height: Double; let dpi: Double
                         let lettering: Lettering; let pages: [Page] }

let args = CommandLine.arguments
guard args.count == 3 else { FileHandle.standardError.write("usage: render_pages.swift <spec.json> <out.pdf>\n".data(using: .utf8)!); exit(2) }
let spec = try JSONDecoder().decode(Spec.self, from: Data(contentsOf: URL(fileURLWithPath: args[1])))
let s = 72.0 / spec.dpi                       // pixels at 300 dpi -> PDF points
var media = CGRect(x: 0, y: 0, width: spec.width * s, height: spec.height * s)
guard let ctx = CGContext(URL(fileURLWithPath: args[2]) as CFURL, mediaBox: &media, nil) else { exit(1) }

// page pixel rect (origin top-left) -> PDF rect (origin bottom-left, points)
func pdfRect(_ x: Double, _ y: Double, _ w: Double, _ h: Double) -> CGRect {
    CGRect(x: x * s, y: (spec.height - y - h) * s, width: w * s, height: h * s)
}

func loadImage(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

func drawCover(_ img: CGImage, in b: Box) {
    // crop the image to the box's aspect ratio (centred), then draw it into the box
    let ia = Double(img.width) / Double(img.height), ba = b.w / b.h
    var crop = CGRect(x: 0, y: 0, width: img.width, height: img.height)
    if ia > ba { let w = Double(img.height) * ba; crop = CGRect(x: (Double(img.width) - w) / 2, y: 0, width: w, height: Double(img.height)) }
    else { let h = Double(img.width) / ba; crop = CGRect(x: 0, y: (Double(img.height) - h) / 2, width: Double(img.width), height: h) }
    if let c = img.cropping(to: crop.integral) { ctx.draw(c, in: pdfRect(b.x, b.y, b.w, b.h)) }
}

func attributed(_ text: String, size: Double, color: CGColor) -> NSAttributedString {
    let font = CTFontCreateWithName("Georgia" as CFString, size, nil)
    var lh = CGFloat(size * spec.lettering.line_height)
    let settings = [CTParagraphStyleSetting(spec: .minimumLineHeight, valueSize: MemoryLayout<CGFloat>.size, value: &lh),
                    CTParagraphStyleSetting(spec: .maximumLineHeight, valueSize: MemoryLayout<CGFloat>.size, value: &lh)]
    let para = CTParagraphStyleCreate(settings, settings.count)
    return NSAttributedString(string: text, attributes: [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): color,
        NSAttributedString.Key(kCTParagraphStyleAttributeName as String): para])
}

func drawText(_ t: TextItem, in panel: Box) {
    let L = spec.lettering
    let fontPt = L.font_px * s, padPt = L.padding_px * s, padYPt = L.padding_px * 0.6 * s
    let maxTextW = (min(L.max_width_px, panel.w - 2 * L.margin_px) - 2 * L.padding_px) * s
    let str = attributed(t.text, size: fontPt, color: CGColor(gray: 0.07, alpha: 1))
    let fs = CTFramesetterCreateWithAttributedString(str as CFAttributedString)
    let size = CTFramesetterSuggestFrameSizeWithConstraints(fs, CFRange(location: 0, length: 0), nil,
                                                             CGSize(width: maxTextW, height: .greatestFiniteMagnitude), nil)
    let bw = Double(size.width) + 2 * padPt, bh = Double(size.height) + 2 * padYPt
    // panel box in points (origin bottom-left)
    let p = pdfRect(panel.x, panel.y, panel.w, panel.h), m = L.margin_px * s
    var x = Double(p.minX) + m, y = Double(p.maxY) - m - bh                 // default: top left
    let pl = t.placement
    if pl.hasSuffix("right") { x = Double(p.maxX) - m - bw }
    if pl == "top" || pl == "bottom" { x = Double(p.midX) - bw / 2 }
    if pl.hasPrefix("bottom") { y = Double(p.minY) + m }
    if pl.hasPrefix("middle") { y = Double(p.midY) - bh / 2 }
    let r = CGRect(x: x, y: y, width: bw, height: bh)
    let path = t.kind == "speech" ? CGPath(roundedRect: r, cornerWidth: min(bw, bh) / 2, cornerHeight: min(bw, bh) / 2, transform: nil)
                                   : CGPath(rect: r, transform: nil)
    ctx.addPath(path); ctx.setFillColor(CGColor(red: 1, green: 0.99, blue: 0.94, alpha: 1)); ctx.fillPath()
    ctx.addPath(path); ctx.setStrokeColor(CGColor(gray: 0.1, alpha: 1)); ctx.setLineWidth(3 * s); ctx.strokePath()
    let textRect = CGRect(x: x + padPt, y: y + padYPt, width: Double(size.width) + 1, height: Double(size.height))
    let frame = CTFramesetterCreateFrame(fs, CFRange(location: 0, length: 0), CGPath(rect: textRect, transform: nil), nil)
    CTFrameDraw(frame, ctx)
}

for page in spec.pages {
    ctx.beginPDFPage(nil)
    ctx.setFillColor(CGColor(gray: 1, alpha: 1)); ctx.fill(media)
    for pn in page.panels {
        let r = pdfRect(pn.box.x, pn.box.y, pn.box.w, pn.box.h)
        if let path = pn.image, let img = loadImage(path) {
            drawCover(img, in: pn.box)
        } else {
            ctx.setFillColor(CGColor(gray: 0.88, alpha: 1)); ctx.fill(r)
            let label = attributed("Panel \(pn.panel): not generated yet", size: 40 * s, color: CGColor(gray: 0.45, alpha: 1))
            let line = CTLineCreateWithAttributedString(label as CFAttributedString)
            let w = CTLineGetTypographicBounds(line, nil, nil, nil)
            ctx.textPosition = CGPoint(x: Double(r.midX) - w / 2, y: Double(r.midY)); CTLineDraw(line, ctx)
        }
        for t in pn.texts { drawText(t, in: pn.box) }
        ctx.setStrokeColor(CGColor(gray: 0, alpha: 1)); ctx.setLineWidth(5 * s); ctx.stroke(r)
    }
    ctx.endPDFPage()
}
ctx.closePDF()
print("rendered \(spec.pages.count) page(s) -> \(args[2])")

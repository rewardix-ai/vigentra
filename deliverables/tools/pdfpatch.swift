// Patch a PDF by whole pages, keeping every other page, the outline and the links as they are.
//   swift pdfpatch.swift build <base.pdf> <pages.pdf> <out.pdf> <op>...
//     op = replace:<base page>:<pages page> | after:<base page>:<pages page> | append:<pages page>   (1-based)
//   swift pdfpatch.swift render <in.pdf> <page> <out.png>
//   swift pdfpatch.swift info <in.pdf>
import Foundation
import PDFKit
import AppKit

let a = CommandLine.arguments
func open(_ p: String) -> PDFDocument { guard let d = PDFDocument(url: URL(fileURLWithPath: p)) else { fatalError("cannot open \(p)") }; return d }
func outlineCount(_ o: PDFOutline?) -> Int { guard let o = o else { return 0 }; return (0..<o.numberOfChildren).reduce(0) { $0 + 1 + outlineCount(o.child(at: $1)) } }

switch a[1] {
case "info":
    let d = open(a[2]); var links = 0
    for i in 0..<d.pageCount { links += d.page(at: i)!.annotations.count }
    print("\(d.pageCount) pages, \(outlineCount(d.outlineRoot)) outline entries, \(links) annotations")
case "render":
    let d = open(a[2]); let pg = d.page(at: Int(a[3])! - 1)!
    let img = pg.thumbnail(of: NSSize(width: 1400, height: 1400), for: .mediaBox)
    let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[4]))
case "build":
    let base = open(a[2]); let src = open(a[3])      // src stays alive until write: pages of a freed document write out blank
    var shift = 0
    for op in a[5...] {
        let f = op.split(separator: ":").map(String.init)
        switch f[0] {
        case "replace":
            let at = Int(f[1])! - 1 + shift
            base.removePage(at: at); base.insert(src.page(at: Int(f[2])! - 1)!.copy() as! PDFPage, at: at)
        case "after":
            base.insert(src.page(at: Int(f[2])! - 1)!.copy() as! PDFPage, at: Int(f[1])! + shift); shift += 1
        case "append":
            base.insert(src.page(at: Int(f[1])! - 1)!.copy() as! PDFPage, at: base.pageCount)
        default: fatalError("unknown op \(op)")
        }
    }
    guard base.write(to: URL(fileURLWithPath: a[4])) else { fatalError("write failed") }
    print("wrote \(a[4]): \(base.pageCount) pages")
default: fatalError("usage")
}

// Prints every line of text Apple's Vision framework finds in each image: "<file>\t<text>".
// Used to read the camera's own on-screen clock (OSD); runs locally, no network, no model download.
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { print("\(path)\t<unreadable>"); continue }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg).perform([request])
    for obs in request.results ?? [] {
        if let top = obs.topCandidates(1).first { print("\(path)\t\(top.string)") }
    }
}

#!/usr/bin/env swift
import Foundation
import Vision
import ImageIO
import CoreGraphics

struct OCRResult: Codable {
    let text: String
    let lines: [String]
    let average_confidence: Float?
}

enum OCRScriptError: Error, CustomStringConvertible {
    case missingArgument
    case imageLoadFailed(String)
    case visionFailed(String)

    var description: String {
        switch self {
        case .missingArgument:
            return "usage: ocr_image.swift <image_path>"
        case .imageLoadFailed(let path):
            return "failed to load image: \(path)"
        case .visionFailed(let message):
            return message
        }
    }
}

func run() throws {
    guard CommandLine.arguments.count >= 2 else {
        throw OCRScriptError.missingArgument
    }

    let imagePath = CommandLine.arguments[1]
    let imageURL = URL(fileURLWithPath: imagePath)
    guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw OCRScriptError.imageLoadFailed(imagePath)
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    if #available(macOS 13.0, *) {
        request.automaticallyDetectsLanguage = true
    }

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        throw OCRScriptError.visionFailed(error.localizedDescription)
    }

    let observations = request.results ?? []
    var lines: [String] = []
    var confidences: [Float] = []
    for observation in observations {
        if let candidate = observation.topCandidates(1).first {
            lines.append(candidate.string)
            confidences.append(candidate.confidence)
        }
    }

    let averageConfidence: Float? = confidences.isEmpty ? nil : confidences.reduce(0, +) / Float(confidences.count)
    let result = OCRResult(text: lines.joined(separator: "\n"), lines: lines, average_confidence: averageConfidence)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]
    let data = try encoder.encode(result)
    if let json = String(data: data, encoding: .utf8) {
        print(json)
    }
}

do {
    try run()
} catch {
    FileHandle.standardError.write(Data((String(describing: error) + "\n").utf8))
    exit(1)
}

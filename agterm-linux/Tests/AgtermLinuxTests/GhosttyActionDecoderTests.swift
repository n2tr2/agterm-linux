import CGtk
import Testing
@testable import AgtermLinux

@Suite("libghostty action payload decoding")
struct GhosttyActionDecoderTests {
    @Test("URL decoding honors the explicit byte length")
    func lengthDelimitedURL() {
        let expected = "https://example.test/path?q=1"
        var bytes = expected.utf8.map { CChar(bitPattern: $0) }
        bytes.append(contentsOf: [CChar(bitPattern: 0x58), CChar(bitPattern: 0x59), 0])

        let decoded = bytes.withUnsafeBufferPointer {
            GhosttyActionDecoder.utf8String($0.baseAddress, length: UInt(expected.utf8.count))
        }
        #expect(decoded == expected)
        #expect(GhosttyActionDecoder.utf8String(nil, length: 0) == "")
        #expect(GhosttyActionDecoder.utf8String(nil, length: 1) == nil)

        let invalid = [CChar(bitPattern: 0xC3), CChar(bitPattern: 0x28)]
        #expect(invalid.withUnsafeBufferPointer {
            GhosttyActionDecoder.utf8String($0.baseAddress, length: UInt($0.count))
        } == nil)
    }

    @Test("link hover follows the payload length, not the pointer")
    func linkHoverPayloadLength() {
        // libghostty ends a hover with an EMPTY string, never a null pointer, so the
        // pointer stays valid (it addresses the NUL terminator) and only the length
        // tells "over a link" apart from "hover ended".
        let cleared = "".withCString { GhosttyActionDecoder.linkHoverActive($0, length: 0) }
        #expect(!cleared)

        let url = "https://example.test/path?q=1"
        let hovered = url.withCString {
            GhosttyActionDecoder.linkHoverActive($0, length: UInt(url.utf8.count))
        }
        #expect(hovered)

        // defensive: a null pointer is not a hover regardless of the reported length.
        #expect(!GhosttyActionDecoder.linkHoverActive(nil, length: 0))
        #expect(!GhosttyActionDecoder.linkHoverActive(nil, length: 4))

        // the shape the action arm actually passes: the raw payload, whose `size_t` length the
        // importer maps to Int. Covers the field selection and the conversion, not just the predicate.
        let clearedPayload = "".withCString {
            GhosttyActionDecoder.linkHoverActive(ghostty_action_mouse_over_link_s(url: $0, len: 0))
        }
        #expect(!clearedPayload)
        let hoveredPayload = url.withCString {
            GhosttyActionDecoder.linkHoverActive(
                ghostty_action_mouse_over_link_s(url: $0, len: url.utf8.count))
        }
        #expect(hoveredPayload)
    }

    @Test("lossy decoding honors the explicit byte length and substitutes invalid bytes")
    func lossyLengthDelimitedText() {
        let expected = "selected text"
        var bytes = expected.utf8.map { CChar(bitPattern: $0) }
        bytes.append(contentsOf: [CChar(bitPattern: 0x58), CChar(bitPattern: 0x59), 0])

        // the reported length wins over any trailing bytes, NUL terminator or not.
        #expect(bytes.withUnsafeBufferPointer {
            GhosttyActionDecoder.lossyUTF8String($0.baseAddress, length: UInt(expected.utf8.count))
        } == expected)

        // an empty payload reads as "no selection", not as an empty string.
        #expect(bytes.withUnsafeBufferPointer {
            GhosttyActionDecoder.lossyUTF8String($0.baseAddress, length: 0)
        } == nil)
        #expect(GhosttyActionDecoder.lossyUTF8String(nil, length: 0) == nil)
        #expect(GhosttyActionDecoder.lossyUTF8String(nil, length: 4) == nil)

        // an over-Int.max length would trap the Int conversion; report nil instead.
        #expect(bytes.withUnsafeBufferPointer {
            GhosttyActionDecoder.lossyUTF8String($0.baseAddress, length: UInt(Int.max) + 1)
        } == nil)

        // readSelection's contract: nil means text_len == 0, never "the bytes start with a NUL" —
        // an embedded NUL survives instead of truncating the selection the way String(cString:) did.
        let embedded = [CChar(bitPattern: 0x61), 0, CChar(bitPattern: 0x62)]
        #expect(embedded.withUnsafeBufferPointer {
            GhosttyActionDecoder.lossyUTF8String($0.baseAddress, length: UInt($0.count))
        } == "a\u{0}b")

        // unlike utf8String, undecodable bytes become U+FFFD rather than nil, so a
        // selection is never reported as absent just because it does not decode.
        let invalid = [CChar(bitPattern: 0xC3), CChar(bitPattern: 0x28)]
        #expect(invalid.withUnsafeBufferPointer {
            GhosttyActionDecoder.lossyUTF8String($0.baseAddress, length: UInt($0.count))
        } == "\u{FFFD}(")
    }
}

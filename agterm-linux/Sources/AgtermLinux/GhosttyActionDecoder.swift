import CGtk

enum GhosttyActionDecoder {
    static func utf8String(_ bytes: UnsafePointer<CChar>?, length: UInt) -> String? {
        guard length <= UInt(Int.max) else { return nil }
        guard length > 0 else { return "" }
        guard let bytes else { return nil }
        let buffer = UnsafeRawBufferPointer(start: bytes, count: Int(length))
        return String(bytes: buffer, encoding: .utf8)
    }

    /// Decode a length-delimited payload lossily: undecodable bytes become U+FFFD rather than nil, and
    /// an absent, empty, or over-`Int.max` payload is nil (`utf8String` instead maps length 0 to `""`).
    static func lossyUTF8String(_ bytes: UnsafePointer<CChar>?, length: UInt) -> String? {
        guard length <= UInt(Int.max), length > 0, let bytes else { return nil }
        return String(decoding: UnsafeRawBufferPointer(start: bytes, count: Int(length)), as: UTF8.self)
    }

    /// Whether a `mouse_over_link` payload says the pointer is on a hyperlink. libghostty ends a hover
    /// with an EMPTY string, never a null pointer, so only `length` tells the two apart.
    static func linkHoverActive(_ bytes: UnsafePointer<CChar>?, length: UInt) -> Bool {
        length > 0 && bytes != nil
    }

    /// Overload over the raw action payload, so the field read and the `size_t` → `UInt` conversion sit
    /// in the tested seam instead of at the untestable action arm.
    static func linkHoverActive(_ value: ghostty_action_mouse_over_link_s) -> Bool {
        linkHoverActive(value.url, length: UInt(bitPattern: value.len))
    }
}

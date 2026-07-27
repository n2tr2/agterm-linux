import CGtk
import Testing
@testable import AgtermLinux

@Suite("embedded libghostty surface cursor")
struct GhosttySurfaceCursorTests {
    @Test("a hyperlink hover outranks the shape libghostty asked for")
    func hoverOutranksShape() {
        #expect(GhosttySurfaceCursor.name(mouseVisible: true, overLink: true, shapeName: "text") == "pointer")
        #expect(GhosttySurfaceCursor.name(mouseVisible: true, overLink: true, shapeName: "crosshair") == "pointer")
    }

    @Test("the requested shape applies when the pointer is not on a link")
    func shapeAppliesWithoutHover() {
        #expect(GhosttySurfaceCursor.name(mouseVisible: true, overLink: false, shapeName: "text") == "text")
        #expect(GhosttySurfaceCursor.name(mouseVisible: true, overLink: false, shapeName: "crosshair") == "crosshair")
    }

    @Test("hiding the pointer while typing outranks both")
    func hiddenOutranksEverything() {
        #expect(GhosttySurfaceCursor.name(mouseVisible: false, overLink: true, shapeName: "text") == "none")
        #expect(GhosttySurfaceCursor.name(mouseVisible: false, overLink: false, shapeName: "crosshair") == "none")
    }

    @Test("every libghostty shape maps to its GTK named cursor")
    func shapeNamesCoverTheLibghosttySet() {
        // The full `ghostty_action_mouse_shape_e` set from the vendored header, in header order. GTK
        // takes CSS cursor names directly, so each shape's name is its CSS spelling.
        let expected: [(ghostty_action_mouse_shape_e, String)] = [
            (GHOSTTY_MOUSE_SHAPE_DEFAULT, "default"),
            (GHOSTTY_MOUSE_SHAPE_CONTEXT_MENU, "context-menu"),
            (GHOSTTY_MOUSE_SHAPE_HELP, "help"),
            (GHOSTTY_MOUSE_SHAPE_POINTER, "pointer"),
            (GHOSTTY_MOUSE_SHAPE_PROGRESS, "progress"),
            (GHOSTTY_MOUSE_SHAPE_WAIT, "wait"),
            (GHOSTTY_MOUSE_SHAPE_CELL, "cell"),
            (GHOSTTY_MOUSE_SHAPE_CROSSHAIR, "crosshair"),
            (GHOSTTY_MOUSE_SHAPE_TEXT, "text"),
            (GHOSTTY_MOUSE_SHAPE_VERTICAL_TEXT, "vertical-text"),
            (GHOSTTY_MOUSE_SHAPE_ALIAS, "alias"),
            (GHOSTTY_MOUSE_SHAPE_COPY, "copy"),
            (GHOSTTY_MOUSE_SHAPE_MOVE, "move"),
            (GHOSTTY_MOUSE_SHAPE_NO_DROP, "no-drop"),
            (GHOSTTY_MOUSE_SHAPE_NOT_ALLOWED, "not-allowed"),
            (GHOSTTY_MOUSE_SHAPE_GRAB, "grab"),
            (GHOSTTY_MOUSE_SHAPE_GRABBING, "grabbing"),
            (GHOSTTY_MOUSE_SHAPE_ALL_SCROLL, "all-scroll"),
            (GHOSTTY_MOUSE_SHAPE_COL_RESIZE, "col-resize"),
            (GHOSTTY_MOUSE_SHAPE_ROW_RESIZE, "row-resize"),
            (GHOSTTY_MOUSE_SHAPE_N_RESIZE, "n-resize"),
            (GHOSTTY_MOUSE_SHAPE_E_RESIZE, "e-resize"),
            (GHOSTTY_MOUSE_SHAPE_S_RESIZE, "s-resize"),
            (GHOSTTY_MOUSE_SHAPE_W_RESIZE, "w-resize"),
            (GHOSTTY_MOUSE_SHAPE_NE_RESIZE, "ne-resize"),
            (GHOSTTY_MOUSE_SHAPE_NW_RESIZE, "nw-resize"),
            (GHOSTTY_MOUSE_SHAPE_SE_RESIZE, "se-resize"),
            (GHOSTTY_MOUSE_SHAPE_SW_RESIZE, "sw-resize"),
            (GHOSTTY_MOUSE_SHAPE_EW_RESIZE, "ew-resize"),
            (GHOSTTY_MOUSE_SHAPE_NS_RESIZE, "ns-resize"),
            (GHOSTTY_MOUSE_SHAPE_NESW_RESIZE, "nesw-resize"),
            (GHOSTTY_MOUSE_SHAPE_NWSE_RESIZE, "nwse-resize"),
            (GHOSTTY_MOUSE_SHAPE_ZOOM_IN, "zoom-in"),
            (GHOSTTY_MOUSE_SHAPE_ZOOM_OUT, "zoom-out"),
        ]
        for (shape, name) in expected {
            #expect(GhosttySurfaceCursor.shapeName(for: shape) == name)
        }
        // The table must stay exhaustive: the last constant's raw value is the set's upper bound, so a
        // `GHOSTTY_REV` bump that adds a shape trips this instead of silently landing in the fallback.
        #expect(GHOSTTY_MOUSE_SHAPE_ZOOM_OUT.rawValue == UInt32(expected.count - 1))
    }

    @Test("the arrow shape is honored, not folded into the I-beam fallback")
    func defaultShapeDrawsTheArrow() {
        // `.default` is reachable twice over: a mouse-reporting TUI asks for it on every eligible key
        // event, and `printf '\e]22;default\a'` requests it outright. Mapping it through the `text`
        // fallback showed an I-beam inside vim/htop where upstream's GTK frontend shows the arrow.
        #expect(GhosttySurfaceCursor.shapeName(for: GHOSTTY_MOUSE_SHAPE_DEFAULT) == "default")
        #expect(
            GhosttySurfaceCursor.name(
                mouseVisible: true, overLink: false,
                shapeName: GhosttySurfaceCursor.shapeName(for: GHOSTTY_MOUSE_SHAPE_DEFAULT)) == "default")
    }

    @Test("a shape a future libghostty adds falls back to the terminal's resting I-beam")
    func unknownShapeFallsBackToText() {
        #expect(GhosttySurfaceCursor.shapeName(for: ghostty_action_mouse_shape_e(rawValue: 9_999)) == "text")
    }

    @Test("a hover-end payload restores the shape instead of latching the hand")
    func hoverEndPayloadRestoresShape() {
        let url = "https://example.test/path"
        let hovered = url.withCString {
            GhosttySurfaceCursor.name(
                mouseVisible: true,
                overLink: GhosttyActionDecoder.linkHoverActive(
                    ghostty_action_mouse_over_link_s(url: $0, len: url.utf8.count)),
                shapeName: "text")
        }
        #expect(hovered == "pointer")

        // libghostty ends the hover with an EMPTY string, not a null pointer: the payload the old
        // `url != nil` read as "still over a link", which pinned the hand for the surface's lifetime.
        let cleared = "".withCString {
            GhosttySurfaceCursor.name(
                mouseVisible: true,
                overLink: GhosttyActionDecoder.linkHoverActive(
                    ghostty_action_mouse_over_link_s(url: $0, len: 0)),
                shapeName: "text")
        }
        #expect(cleared == "text")
    }
}

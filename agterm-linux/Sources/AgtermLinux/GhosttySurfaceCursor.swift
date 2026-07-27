// The pointer shape shown over a terminal surface. GTK delivers the three libghostty signals that
// decide it independently (MOUSE_VISIBILITY, MOUSE_OVER_LINK, MOUSE_SHAPE), so the surface keeps all
// three and re-derives the winner here; keeping the rule host-free is what makes it testable, since
// its only caller ends in gtk_widget_set_cursor_from_name.
import CGtk

enum GhosttySurfaceCursor {
    /// Precedence, highest first: the pointer hidden while typing beats everything; a hyperlink hover
    /// beats the shape libghostty last asked for (an application's OSC 22 request included);
    /// otherwise the requested shape applies.
    /// The link-hover layer is a port-local addition — upstream's GTK frontend drives the cursor from
    /// visibility + shape alone and spends `mouse_over_link` on the link preview — so this is the one
    /// place to change if the port ever converges on upstream.
    static func name(mouseVisible: Bool, overLink: Bool, shapeName: String) -> String {
        guard mouseVisible else { return "none" }
        return overLink ? "pointer" : shapeName
    }

    /// Map a libghostty mouse shape (GHOSTTY_ACTION_MOUSE_SHAPE) to the GTK named cursor that draws it.
    /// ghostty's shapes are named after CSS cursors and `gtk_widget_set_cursor_from_name` takes those
    /// names directly, so the mapping is one-to-one across the whole libghostty set — `DEFAULT`
    /// included: it is the ordinary arrow, which a mouse-reporting TUI asks for on every eligible key
    /// event (`SurfaceMouse.keyToMouseShape`) and which `printf '\e]22;default\a'` requests explicitly.
    /// It is spelled out rather than left to the fallback because upstream's GTK frontend maps it to
    /// the arrow, and folding it into a `text` fallback showed an I-beam inside vim/htop instead.
    /// The `default:` arm therefore only catches a shape a future `GHOSTTY_REV` adds; it resolves to
    /// `text`, the terminal's own resting shape and this surface's initial one, since an unknown shape
    /// over a terminal is likelier to want the I-beam than the arrow.
    static func shapeName(for shape: ghostty_action_mouse_shape_e) -> String {
        switch shape {
        case GHOSTTY_MOUSE_SHAPE_ALIAS: return "alias"
        case GHOSTTY_MOUSE_SHAPE_ALL_SCROLL: return "all-scroll"
        case GHOSTTY_MOUSE_SHAPE_CELL: return "cell"
        case GHOSTTY_MOUSE_SHAPE_COL_RESIZE: return "col-resize"
        case GHOSTTY_MOUSE_SHAPE_CONTEXT_MENU: return "context-menu"
        case GHOSTTY_MOUSE_SHAPE_COPY: return "copy"
        case GHOSTTY_MOUSE_SHAPE_CROSSHAIR: return "crosshair"
        case GHOSTTY_MOUSE_SHAPE_DEFAULT: return "default"
        case GHOSTTY_MOUSE_SHAPE_E_RESIZE: return "e-resize"
        case GHOSTTY_MOUSE_SHAPE_EW_RESIZE: return "ew-resize"
        case GHOSTTY_MOUSE_SHAPE_HELP: return "help"
        case GHOSTTY_MOUSE_SHAPE_MOVE: return "move"
        case GHOSTTY_MOUSE_SHAPE_N_RESIZE: return "n-resize"
        case GHOSTTY_MOUSE_SHAPE_NE_RESIZE: return "ne-resize"
        case GHOSTTY_MOUSE_SHAPE_NESW_RESIZE: return "nesw-resize"
        case GHOSTTY_MOUSE_SHAPE_NO_DROP: return "no-drop"
        case GHOSTTY_MOUSE_SHAPE_NOT_ALLOWED: return "not-allowed"
        case GHOSTTY_MOUSE_SHAPE_NS_RESIZE: return "ns-resize"
        case GHOSTTY_MOUSE_SHAPE_NW_RESIZE: return "nw-resize"
        case GHOSTTY_MOUSE_SHAPE_NWSE_RESIZE: return "nwse-resize"
        case GHOSTTY_MOUSE_SHAPE_POINTER: return "pointer"
        case GHOSTTY_MOUSE_SHAPE_PROGRESS: return "progress"
        case GHOSTTY_MOUSE_SHAPE_GRAB: return "grab"
        case GHOSTTY_MOUSE_SHAPE_GRABBING: return "grabbing"
        case GHOSTTY_MOUSE_SHAPE_ROW_RESIZE: return "row-resize"
        case GHOSTTY_MOUSE_SHAPE_S_RESIZE: return "s-resize"
        case GHOSTTY_MOUSE_SHAPE_SE_RESIZE: return "se-resize"
        case GHOSTTY_MOUSE_SHAPE_SW_RESIZE: return "sw-resize"
        case GHOSTTY_MOUSE_SHAPE_TEXT: return "text"
        case GHOSTTY_MOUSE_SHAPE_VERTICAL_TEXT: return "vertical-text"
        case GHOSTTY_MOUSE_SHAPE_W_RESIZE: return "w-resize"
        case GHOSTTY_MOUSE_SHAPE_WAIT: return "wait"
        case GHOSTTY_MOUSE_SHAPE_ZOOM_IN: return "zoom-in"
        case GHOSTTY_MOUSE_SHAPE_ZOOM_OUT: return "zoom-out"
        default: return "text"
        }
    }
}

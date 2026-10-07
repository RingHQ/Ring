import org.kde.layershell as LayerShell

// The ring's window as a layer-shell surface, for Wayland.
OverlayWindow {
    LayerShell.Window.scope: "ring"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom
        | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    // -1: cover panels too, so local coordinates match the monitor's.
    LayerShell.Window.exclusionZone: -1
    // Exclusive: keys pressed while the menu is open select actions instead
    // of reaching the application underneath.
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive
}

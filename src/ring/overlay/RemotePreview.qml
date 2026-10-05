import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

// The preview on its own, for a target on another monitor than the one the
// ring is on. It takes no input: pointer and keys stay with Overlay.qml.
Window {
    id: root

    property bool previewVisible: false
    property real previewX: 0
    property real previewY: 0
    property real previewWidth: 0
    property real previewHeight: 0
    property int previewRadius: 10
    property int previewBorder: 4
    property var previewCurve: [0.22, 1, 0.47, 1, 1, 1]
    property color previewBorderColor: "#5e9cff"
    property color previewFill: Qt.rgba(0, 0, 0, 0.15)
    property int previewMs: 250
    // False while Python positions things, as in Overlay.qml.
    property bool animate: false

    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput

    LayerShell.Window.scope: "ring-preview"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom
        | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone

    Preview {
        x: root.previewX
        y: root.previewY
        width: root.previewWidth
        height: root.previewHeight
        shown: root.previewVisible
        borderColor: root.previewBorderColor
        fillColor: root.previewFill
        cornerRadius: root.previewRadius
        borderThickness: root.previewBorder
        curve: root.previewCurve
        duration: root.animate ? root.previewMs : 0
    }
}

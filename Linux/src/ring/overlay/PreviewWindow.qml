import QtQuick
import QtQuick.Window

// The preview on its own, for a target on another monitor than the one the
// ring is on. It takes no input: pointer and keys stay with the ring's
// window. As with OverlayWindow.qml, RemotePreview.qml makes it a
// layer-shell surface and X11 uses it as it is.
Window {
    id: root

    property bool previewVisible: false
    property real previewX: 0
    property real previewY: 0
    property real previewWidth: 0
    property real previewHeight: 0
    property int previewRadius: 10
    property int previewBorder: 4
    property bool previewGlass: false
    property var previewCurve: [0.22, 1, 0.47, 1, 1, 1]
    property color previewBorderColor: "#5e9cff"
    property color previewFill: Qt.rgba(0, 0, 0, 0.15)
    property int previewMs: 250
    // False while Python positions things, as in OverlayWindow.qml.
    property bool animate: false

    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput

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
        glass: root.previewGlass
        curve: root.previewCurve
        duration: root.animate ? root.previewMs : 0
    }
}

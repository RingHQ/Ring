import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

// One transparent surface covering a whole monitor. All state is pushed in
// from Python (overlay/radial.py); input goes back through `bridge`.
Window {
    id: root

    property real menuX: 0
    property real menuY: 0
    property int menuRadius: 50
    property int menuThickness: 22
    property real filledScale: 0.85
    // 0: nothing lit, 1: a segment at `angle`, 2: the whole ring.
    property int highlight: 0
    // Bearing of the segment, degrees clockwise from north. Not wrapped to
    // 0..360, so that it can always turn the short way round.
    property real angle: 0
    property bool angleAnimated: false

    property bool previewVisible: false
    property real previewX: 0
    property real previewY: 0
    property real previewWidth: 0
    property real previewHeight: 0
    property int previewRadius: 10
    property int previewBorder: 4
    property var previewCurve: [0.22, 1, 0.47, 1, 1, 1]

    property color accent: "#5e9cff"
    property color accent2: accent
    property color ringColor: Qt.rgba(0.11, 0.11, 0.12, 0.5)
    property bool menuShown: true
    property color previewBorderColor: accent
    property color previewFill: Qt.rgba(0, 0, 0, 0.15)
    // True when the compositor blurs what is behind the ring / the preview.
    property bool menuBlurred: false
    property bool previewBlurred: false

    property int previewMs: 250
    property int sizeMs: 200
    property int angleMs: 200
    property int appearMs: 100
    // False while Python positions things, so nothing animates in from
    // wherever it happened to be before.
    property bool animate: false

    function pushPreview() {
        bridge.previewMoved(preview.x, preview.y, preview.width, preview.height, preview.opacity > 0.4);
    }

    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint

    LayerShell.Window.scope: "looplinux"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom
        | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    // -1: cover panels too, so local coordinates match the monitor's.
    LayerShell.Window.exclusionZone: -1
    // Exclusive: keys pressed while the menu is open select actions instead
    // of reaching the application underneath.
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive

    onVisibleChanged: {
        if (visible) {
            input.forceActiveFocus();
            if (appearMs > 0) {
                appear.restart();
            }
        }
    }

    Item {
        id: input
        anchors.fill: parent
        focus: true

        Keys.onPressed: event => {
            if (!event.isAutoRepeat) {
                bridge.keyPressed(event.key, (event.modifiers & Qt.ShiftModifier) !== 0);
            }
            event.accepted = true;
        }
        Keys.onReleased: event => {
            if (!event.isAutoRepeat) {
                bridge.keyReleased(event.key, event.nativeScanCode);
            }
            event.accepted = true;
        }

        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onPositionChanged: mouse => bridge.pointerMoved(mouse.x, mouse.y)
            onPressed: mouse => bridge.mouseClicked(mouse.button === Qt.RightButton)
        }
    }

    Preview {
        id: preview
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

        // Several of these change in the same frame; callLater folds them
        // into one update of the blurred region.
        onXChanged: Qt.callLater(root.pushPreview)
        onYChanged: Qt.callLater(root.pushPreview)
        onWidthChanged: Qt.callLater(root.pushPreview)
        onHeightChanged: Qt.callLater(root.pushPreview)
        onOpacityChanged: Qt.callLater(root.pushPreview)
    }

    RadialMenu {
        id: menu
        x: Math.round(root.menuX - width / 2)
        y: Math.round(root.menuY - height / 2)
        outerRadius: root.menuRadius
        thickness: root.menuThickness
        filledScale: root.filledScale
        highlight: root.highlight
        angle: root.angle
        angleAnimated: root.animate && root.angleAnimated
        visible: root.menuShown
        accent: root.accent
        accent2: root.accent2
        ringColor: root.ringColor
        angleMs: root.angleMs
        sizeMs: root.animate ? root.sizeMs : 0
    }

    NumberAnimation {
        id: appear
        target: menu
        property: "opacity"
        from: 0
        to: 1
        duration: root.appearMs
    }
}

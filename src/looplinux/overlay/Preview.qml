import QtQuick

// Loop's preview: a dimmed, rounded outline in the accent color showing where
// the window will land. It fades in slightly smaller than its target and
// glides to each new one.
Rectangle {
    id: preview

    property bool shown: false
    property color accent: "#5e9cff"
    property int cornerRadius: 10
    property int borderThickness: 4
    property bool blurred: false
    property var curve: [0.22, 1, 0.47, 1, 1, 1]
    property int duration: 250

    opacity: shown ? 1 : 0
    radius: cornerRadius
    color: blurred ? Qt.rgba(accent.r, accent.g, accent.b, 0.1) : Qt.rgba(0, 0, 0, 0.15)
    border.color: accent
    border.width: borderThickness

    // A hairline inside the border, as in Loop.
    Rectangle {
        anchors.fill: parent
        anchors.margins: parent.border.width
        radius: Math.max(parent.radius - parent.border.width, 0)
        color: "transparent"
        border.color: Qt.rgba(1, 1, 1, 0.12)
        border.width: 1
    }

    Behavior on x { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on y { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on width { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on height { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on opacity { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
}

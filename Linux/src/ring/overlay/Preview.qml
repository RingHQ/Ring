import QtQuick

// The preview: a dimmed, rounded outline in the accent color showing where
// the window will land. It fades in slightly smaller than its target and
// glides to each new one.
//
// With `glass` it is a pane of clear glass instead: lit from above, with a
// bright rim, light gathering along its edges and a halo in the accent
// color. It is built from plain rectangles only, so gliding costs nothing;
// what is behind it is blurred by the compositor (see overlay/radial.py).
Rectangle {
    id: preview

    property bool shown: false
    property color borderColor: "#5e9cff"
    property color fillColor: Qt.rgba(0, 0, 0, 0.15)
    property int cornerRadius: 10
    property int borderThickness: 4
    property bool glass: false
    property var curve: [0.22, 1, 0.47, 1, 1, 1]
    property int duration: 250

    opacity: shown ? 1 : 0
    radius: cornerRadius
    color: glass ? "transparent" : fillColor
    border.color: borderColor
    border.width: glass ? 0 : borderThickness

    // A hairline inside the border.
    Rectangle {
        visible: !preview.glass
        anchors.fill: parent
        anchors.margins: parent.border.width
        radius: Math.max(parent.radius - parent.border.width, 0)
        color: "transparent"
        border.color: Qt.rgba(1, 1, 1, 0.12)
        border.width: 1
    }

    Item {
        anchors.fill: parent
        visible: preview.glass

        // The halo: the accent color, fading outwards.
        Repeater {
            model: 5
            Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -(index + 1)
                radius: preview.radius + index + 1
                color: "transparent"
                border.width: 1
                border.color: Qt.rgba(preview.borderColor.r, preview.borderColor.g,
                                      preview.borderColor.b, 0.34 * Math.pow(0.58, index))
            }
        }
        // The pane: brightest where the light falls on it.
        Rectangle {
            anchors.fill: parent
            radius: preview.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.22) }
                GradientStop { position: 0.3; color: Qt.rgba(1, 1, 1, 0.07) }
                GradientStop { position: 0.75; color: Qt.rgba(1, 1, 1, 0.05) }
                GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, 0.13) }
            }
        }
        // Its tint.
        Rectangle {
            anchors.fill: parent
            radius: preview.radius
            color: preview.fillColor
        }
        // Light gathering along the edges, as in thick glass.
        Repeater {
            model: 8
            Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: index + 1
                radius: Math.max(preview.radius - index - 1, 0)
                color: "transparent"
                border.width: 1
                border.color: Qt.rgba(1, 1, 1, 0.2 * Math.pow(0.66, index))
            }
        }
        // The rim.
        Rectangle {
            anchors.fill: parent
            radius: preview.radius
            color: "transparent"
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.62)
        }
        // Glints on the top and the bottom edge.
        Rectangle {
            x: preview.radius
            y: 1
            width: Math.max(parent.width - 2 * preview.radius, 0)
            height: 2
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0) }
                GradientStop { position: 0.25; color: Qt.rgba(1, 1, 1, 0.85) }
                GradientStop { position: 0.6; color: Qt.rgba(1, 1, 1, 0.25) }
                GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, 0) }
            }
        }
        Rectangle {
            x: preview.radius
            y: parent.height - 2
            width: Math.max(parent.width - 2 * preview.radius, 0)
            height: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0) }
                GradientStop { position: 0.45; color: Qt.rgba(1, 1, 1, 0.2) }
                GradientStop { position: 0.8; color: Qt.rgba(1, 1, 1, 0.6) }
                GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, 0) }
            }
        }
    }

    Behavior on x { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on y { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on width { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on height { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
    Behavior on opacity { enabled: preview.duration > 0; NumberAnimation { duration: preview.duration; easing.type: Easing.BezierSpline; easing.bezierCurve: preview.curve } }
}

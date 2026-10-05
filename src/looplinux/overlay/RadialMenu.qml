import QtQuick

// Loop's radial menu: a 100 px frosted ring around the cursor. The selected
// direction lights up as a 45 degree segment in the accent color and glides
// round to the next one; actions without a direction light up the whole
// ring, which shrinks a little while they are selected.
Item {
    id: menu

    property int outerRadius: 50
    property int thickness: 22
    property real filledScale: 0.85
    property int highlight: 0
    property real angle: 0
    property bool angleAnimated: false
    property color accent: "#5e9cff"
    property color accent2: accent
    property color ringColor: Qt.rgba(0.11, 0.11, 0.12, 0.5)
    property int angleMs: 200
    property int sizeMs: 200

    // The angle on screen right now; it chases `angle`.
    property real shownAngle: angle

    // Room around the ring for its shadow.
    readonly property int margin: 16

    width: (outerRadius + margin) * 2
    height: width
    scale: highlight === 2 ? filledScale : 1

    Behavior on shownAngle {
        enabled: menu.angleAnimated && menu.angleMs > 0
        NumberAnimation { duration: menu.angleMs; easing.type: Easing.BezierSpline; easing.bezierCurve: [0.22, 1, 0.36, 1, 1, 1] }
    }
    Behavior on scale {
        enabled: menu.sizeMs > 0
        NumberAnimation { duration: menu.sizeMs; easing.type: Easing.OutQuad }
    }

    onShownAngleChanged: canvas.requestPaint()
    onHighlightChanged: canvas.requestPaint()
    onAccentChanged: canvas.requestPaint()
    onAccent2Changed: canvas.requestPaint()
    onOuterRadiusChanged: canvas.requestPaint()
    onThicknessChanged: canvas.requestPaint()
    onRingColorChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var c = width / 2;
            var outer = menu.outerRadius;
            var inner = outer - menu.thickness;
            var mid = (outer + inner) / 2;

            // The ring: dark glass with a soft shadow, more opaque when the
            // compositor does not blur what is behind it.
            ctx.save();
            ctx.shadowColor = Qt.rgba(0, 0, 0, 0.35);
            ctx.shadowBlur = 10;
            ctx.beginPath();
            ctx.arc(c, c, mid, 0, 2 * Math.PI);
            ctx.lineWidth = menu.thickness;
            ctx.strokeStyle = menu.ringColor;
            ctx.stroke();
            ctx.restore();

            if (menu.highlight !== 0) {
                var gradient = ctx.createLinearGradient(c - outer, c - outer, c + outer, c + outer);
                gradient.addColorStop(0, menu.accent.toString());
                gradient.addColorStop(1, menu.accent2.toString());
                ctx.beginPath();
                if (menu.highlight === 2) {
                    ctx.arc(c, c, mid, 0, 2 * Math.PI);
                } else {
                    // Canvas angles start at east and grow clockwise on screen.
                    var center = (menu.shownAngle - 90) * Math.PI / 180;
                    var half = 22.5 * Math.PI / 180;
                    ctx.arc(c, c, mid, center - half, center + half, false);
                }
                ctx.lineWidth = menu.thickness;
                ctx.strokeStyle = gradient;
                ctx.stroke();
            }

            // Hairlines on both rims.
            ctx.lineWidth = 1;
            ctx.strokeStyle = Qt.rgba(1, 1, 1, 0.14);
            ctx.beginPath();
            ctx.arc(c, c, outer - 0.5, 0, 2 * Math.PI);
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(c, c, inner + 0.5, 0, 2 * Math.PI);
            ctx.stroke();
        }
    }
}

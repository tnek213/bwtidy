// The chart of the start page's Usage section (dev/demo.md, Usage stats): the active demo
// sessions per hour with the capacity line and the hours when visitors were turned away. The
// numbers and tables are in the page already, this only adds the picture.
(function () {
  "use strict";
  var box = document.getElementById("usage-chart");
  if (!box || typeof uPlot === "undefined" || !window.fetch) {
    return;
  }
  var HOUR = 3600;

  function color(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // every hour of the chart's days, null where no sample was taken, so a gap stays a gap
  function series(demo) {
    var end = Math.floor(Date.now() / 1000 / HOUR) * HOUR;
    var start = end - demo.chart_days * 24 * HOUR;
    var byHour = {};
    demo.hours.forEach(function (hour) {
      byHour[Date.parse(hour.hour) / 1000] = hour;
    });
    var x = [];
    var active = [];
    var capacity = [];
    var away = [];
    var counts = [];
    for (var t = start; t <= end; t += HOUR) {
      var hour = byHour[t];
      x.push(t);
      active.push(hour ? hour.active : null);
      capacity.push(hour ? hour.capacity : null);
      // a marker on the capacity line in each hour with a visitor turned away
      away.push(hour && hour.turned_away > 0 ? hour.capacity : null);
      counts.push(hour ? hour.turned_away : null);
    }
    return { data: [x, active, capacity, away], counts: counts };
  }

  function options(width, counts) {
    var muted = color("--muted");
    var line = color("--line");
    var awayColor = color("--chart-away");
    var axis = {
      stroke: muted,
      grid: { stroke: line, width: 1 },
      ticks: { stroke: line, width: 1 },
    };
    return {
      width: width,
      height: 240,
      tzDate: function (ts) {
        return uPlot.tzDate(new Date(ts * 1000), "Etc/UTC");
      },
      scales: {
        y: {
          range: function (u, min, max) {
            return [0, Math.max(1, max) + 1];
          },
        },
      },
      axes: [
        axis,
        Object.assign({}, axis, {
          incrs: [1, 2, 5, 10, 20, 50, 100],
          values: function (u, ticks) {
            return ticks.map(function (tick) {
              return String(tick);
            });
          },
        }),
      ],
      series: [
        { label: "Hour (UTC)" },
        { label: "Active sessions", stroke: color("--chart-active"), width: 2 },
        { label: "Capacity", stroke: muted, width: 1.5, dash: [6, 4] },
        {
          label: "Turned away",
          stroke: awayColor,
          width: 0,
          paths: function () {
            return null;
          },
          points: { show: true, size: 9, fill: awayColor, stroke: awayColor },
          value: function (u, v, seriesIndex, index) {
            return index == null || counts[index] == null ? "" : String(counts[index]);
          },
        },
      ],
    };
  }

  function draw(usage) {
    var found = series(usage.demo);
    var plot = null;
    function render() {
      if (plot) {
        plot.destroy();
      }
      plot = new uPlot(options(box.clientWidth, found.counts), found.data, box);
    }
    box.hidden = false;
    render();
    var width = box.clientWidth;
    window.addEventListener("resize", function () {
      if (box.clientWidth !== width) {
        width = box.clientWidth;
        render();
      }
    });
    if (window.matchMedia) {
      var dark = window.matchMedia("(prefers-color-scheme: dark)");
      if (dark.addEventListener) {
        dark.addEventListener("change", render);
      }
    }
  }

  fetch(box.getAttribute("data-src"), { credentials: "same-origin" })
    .then(function (response) {
      return response.ok ? response.json() : null;
    })
    .then(function (usage) {
      if (usage && usage.demo) {
        draw(usage);
      }
    })
    .catch(function () {});
})();

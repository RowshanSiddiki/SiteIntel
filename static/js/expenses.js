(() => {
  const dataNode = document.getElementById("finance-chart-data");
  if (!dataNode) return;
  const charts = JSON.parse(dataNode.textContent).charts;
  const COLORS = ["#c9ff36", "#6abfff", "#ffbf72", "#bc9cff", "#53d59c", "#ff777c"];
  const NS = "http://www.w3.org/2000/svg";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const label = (iso) => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;
  const fmt = (n) => (Math.abs(n) >= 1000 ? n.toLocaleString("en", { maximumFractionDigits: 0 }) : String(Math.round(n * 10) / 10));

  const el = (tag, attrs = {}, parent) => {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (parent) parent.appendChild(node);
    return node;
  };
  const niceMax = (max) => {
    if (max <= 0) return 1;
    const power = 10 ** Math.floor(Math.log10(max));
    return [1, 2, 2.5, 5, 10].map((m) => m * power).find((v) => v >= max);
  };

  function hasData(chart) {
    return chart && chart.dates.length && chart.series.some((s) => s.values.some((v) => v !== null && v !== undefined));
  }

  function render(host) {
    const chart = charts[host.dataset.chart];
    host.replaceChildren();
    if (!hasData(chart)) {
      const empty = document.createElement("p");
      empty.className = "fa-empty";
      empty.textContent = "Not enough report data yet.";
      host.appendChild(empty);
      return;
    }
    const type = host.dataset.type;
    const width = Math.max(host.clientWidth, 240);
    const height = Math.max(host.clientHeight - 30, 180);
    const m = { top: 14, right: 12, bottom: 28, left: 44 };
    const rightAxis = chart.series.some((s) => s.axis === "right");
    if (rightAxis) m.right = 44;
    const w = width - m.left - m.right, h = height - m.top - m.bottom;
    const hidden = host._hidden || (host._hidden = new Set());
    const visible = chart.series.map((s, i) => ({ ...s, color: COLORS[i % COLORS.length], index: i }))
      .filter((s) => !hidden.has(s.index));
    const stacked = type === "stack";
    const scale = (axis) => {
      const items = visible.filter((s) => (s.axis === "right") === (axis === "right"));
      const values = stacked && axis === "left"
        ? chart.dates.map((_, d) => items.reduce((sum, s) => sum + (s.values[d] || 0), 0))
        : items.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
      return niceMax(Math.max(0, ...values));
    };
    const maxL = scale("left"), maxR = rightAxis ? scale("right") : 1;
    const n = chart.dates.length;
    const barMode = type !== "area" && type !== "combo";
    const x = (i) => (barMode ? m.left + (w / n) * (i + 0.5) : m.left + (n === 1 ? w / 2 : (w * i) / (n - 1)));
    const y = (v, axis) => m.top + h - (v / (axis === "right" ? maxR : maxL)) * h;

    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": host.closest(".fa-card").querySelector("h2").textContent });
    const defs = el("defs", {}, svg);
    visible.forEach((s) => {
      const grad = el("linearGradient", { id: `g-${host.dataset.chart}-${s.index}`, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      el("stop", { offset: "0%", "stop-color": s.color, "stop-opacity": 0.35 }, grad);
      el("stop", { offset: "100%", "stop-color": s.color, "stop-opacity": 0 }, grad);
    });
    for (let t = 0; t <= 4; t++) {
      const yy = m.top + (h * t) / 4;
      el("line", { x1: m.left, x2: m.left + w, y1: yy, y2: yy, class: "fa-grid-line" }, svg);
      el("text", { x: m.left - 8, y: yy + 4, "text-anchor": "end", class: "fa-tick" }, svg).textContent = fmt(maxL * (1 - t / 4));
      if (rightAxis) el("text", { x: m.left + w + 8, y: yy + 4, class: "fa-tick" }, svg).textContent = fmt(maxR * (1 - t / 4));
    }
    const step = Math.ceil(n / Math.max(2, Math.floor(w / 64)));
    chart.dates.forEach((d, i) => {
      if (i % step === 0) el("text", { x: x(i), y: height - 8, "text-anchor": "middle", class: "fa-tick" }, svg).textContent = label(d);
    });

    const slot = w / n;
    if (barMode) {
      const bw = Math.min(28, (slot * 0.7) / (stacked ? 1 : Math.max(visible.length, 1)));
      chart.dates.forEach((_, i) => {
        let base = 0;
        visible.forEach((s, k) => {
          const v = s.values[i];
          if (v === null || v === undefined) return;
          const bh = (v / maxL) * h;
          const bx = stacked ? x(i) - bw / 2 : x(i) - (bw * visible.length) / 2 + k * bw;
          const by = stacked ? m.top + h - ((base + v) / maxL) * h : y(v, "left");
          el("rect", { x: bx, y: by, width: Math.max(bw - 2, 2), height: Math.max(bh, 1), rx: 3, fill: s.color }, svg);
          base += v;
        });
      });
    } else {
      visible.forEach((s) => {
        const axis = s.axis === "right" ? "right" : "left";
        const points = s.values.map((v, i) => (v === null || v === undefined ? null : [x(i), y(v, axis)]));
        const path = points.map((p, i) => (p ? `${i && points[i - 1] ? "L" : "M"}${p[0]},${p[1]}` : "")).join(" ");
        if (type === "area" && points.filter(Boolean).length > 1) {
          const solid = points.filter(Boolean);
          el("path", { d: `M${solid[0][0]},${m.top + h} ${solid.map((p) => `L${p[0]},${p[1]}`).join(" ")} L${solid[solid.length - 1][0]},${m.top + h} Z`, fill: `url(#g-${host.dataset.chart}-${s.index})` }, svg);
        }
        el("path", { d: path, fill: "none", stroke: s.color, "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-dasharray": axis === "right" ? "6 5" : "none" }, svg);
        points.forEach((p) => p && el("circle", { cx: p[0], cy: p[1], r: 3.5, fill: "#101522", stroke: s.color, "stroke-width": 2 }, svg));
      });
    }

    const tip = document.createElement("div");
    tip.className = "fa-tip";
    tip.hidden = true;
    const guide = el("line", { y1: m.top, y2: m.top + h, class: "fa-guide", visibility: "hidden" }, svg);
    svg.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      const px = event.clientX - box.left;
      const i = Math.min(n - 1, Math.max(0, barMode ? Math.floor((px - m.left) / slot) : Math.round(((px - m.left) / (w || 1)) * (n - 1))));
      guide.setAttribute("x1", x(i)); guide.setAttribute("x2", x(i)); guide.setAttribute("visibility", "visible");
      tip.replaceChildren();
      const title = document.createElement("b");
      title.textContent = label(chart.dates[i]);
      tip.appendChild(title);
      visible.forEach((s) => {
        const v = s.values[i];
        const row = document.createElement("span");
        row.textContent = `${s.name}: ${v === null || v === undefined ? "—" : fmt(v)}${chart.unit && s.axis !== "right" ? " " + chart.unit : ""}`;
        row.style.setProperty("--dot", s.color);
        tip.appendChild(row);
      });
      tip.hidden = false;
      tip.style.left = `${Math.min(Math.max(x(i) + 12, 0), width - 170)}px`;
      tip.style.top = `${m.top}px`;
    });
    svg.addEventListener("pointerleave", () => { tip.hidden = true; guide.setAttribute("visibility", "hidden"); });

    const legend = document.createElement("div");
    legend.className = "fa-legend";
    chart.series.forEach((s, i) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = s.name;
      button.style.setProperty("--dot", COLORS[i % COLORS.length]);
      button.setAttribute("aria-pressed", String(!hidden.has(i)));
      button.addEventListener("click", () => {
        hidden.has(i) ? hidden.delete(i) : hidden.add(i);
        if (hidden.size === chart.series.length) hidden.clear();
        render(host);
      });
      legend.appendChild(button);
    });
    host.append(svg, tip, legend);
    const note = [chart.unit && `Unit: ${chart.unit}`, chart.dates.length > 1 && `${label(chart.dates[0])} – ${label(chart.dates[n - 1])}`]
      .filter(Boolean).join(" · ");
    if (note) {
      const caption = document.createElement("small");
      caption.className = "fa-caption";
      caption.textContent = note;
      host.appendChild(caption);
    }
  }

  document.querySelectorAll(".fa-chart").forEach((host) => {
    render(host);
    let lastWidth = host.clientWidth;
    new ResizeObserver(() => {
      if (host.clientWidth !== lastWidth) { lastWidth = host.clientWidth; render(host); }
    }).observe(host);
  });
})();

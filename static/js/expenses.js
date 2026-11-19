(() => {
  const data = JSON.parse(document.getElementById("finance-chart-data")?.textContent || "{}");
  const ns = "http://www.w3.org/2000/svg";
  const colors = ["#c9ff36", "#6abfff", "#ffbf72", "#bc9cff", "#ff8099", "#6fd6b2", "#98a8c4"];
  const format = value => new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(Number(value));
  const compact = value => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
  const svgNode = (name, attrs = {}) => {
    const node = document.createElementNS(ns, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  };
  const textNode = (name, className, text) => {
    const node = document.createElement(name);
    node.className = className;
    node.textContent = text;
    return node;
  };
  const svgText = (parent, attrs, value) => {
    const node = svgNode("text", attrs);
    node.textContent = value;
    parent.appendChild(node);
  };
  const interactive = (node, message, detail) => {
    node.setAttribute("tabindex", "0");
    node.setAttribute("aria-label", message);
    const title = svgNode("title");
    title.textContent = message;
    node.appendChild(title);
    const show = () => { detail.textContent = message; };
    const clear = () => { detail.textContent = "Hover or focus a figure for details"; };
    node.addEventListener("mouseenter", show);
    node.addEventListener("focus", show);
    node.addEventListener("mouseleave", clear);
    node.addEventListener("blur", clear);
  };
  const groups = rows => Object.groupBy
    ? Object.groupBy(rows, row => row.currency)
    : rows.reduce((acc, row) => { (acc[row.currency] ||= []).push(row); return acc; }, {});
  const emptyMessages = {
    "chart-distribution": "Verified invoices or direct costs are needed to show the expense distribution.",
    "chart-trend": "Verified expenses with an identifiable date are needed to show a spending trend.",
    "chart-materials": "Verified material costs are needed for a spending comparison.",
    "chart-quoted-materials": "Verified quotation line items are needed for a comparison.",
    "chart-costs": "Verified material, labour or equipment costs are needed for a comparison.",
    "chart-budget": "A verified budget is needed for a budget comparison.",
  };
  function chart(id, rows, type) {
    const root = document.getElementById(id);
    if (!root) return;
    root.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "finance-chart-empty";
      const icon = document.createElement("span");
      icon.className = "finance-chart-empty-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "◇";
      const copy = document.createElement("div");
      copy.append(textNode("strong", "", "No chart data yet"),
        textNode("p", "", emptyMessages[id] || "No verified figures are available."));
      empty.append(icon, copy);
      root.appendChild(empty);
      return;
    }
    Object.entries(groups(rows)).forEach(([currency, items]) => {
      const wrapper = document.createElement("div");
      wrapper.className = "finance-chart-card";
      const top = document.createElement("div");
      top.className = "finance-chart-top";
      top.append(
        textNode("span", "finance-chart-currency", currency),
        textNode("span", "finance-chart-meta", `${items.length} ${items.length === 1 ? "figure" : "figures"} · verified`)
      );
      wrapper.appendChild(top);
      const detail = textNode("p", "finance-chart-detail", "Hover or focus a figure for details");
      if (type === "donut") {
        const layout = document.createElement("div");
        layout.className = "finance-donut-layout";
        const svg = svgNode("svg", { viewBox: "0 0 240 240", role: "group", "aria-label": `Expense distribution in ${currency}` });
        const sum = items.reduce((total, item) => total + Number(item.value), 0);
        svg.appendChild(svgNode("circle", { cx: 120, cy: 120, r: 86, fill: "none", stroke: "#263146", "stroke-width": 18 }));
        const legend = document.createElement("div");
        legend.className = "finance-chart-legend";
        let offset = 0;
        items.forEach((item, index) => {
          const fraction = sum ? Number(item.value) / sum : 0;
          const color = colors[index % colors.length];
          const ring = svgNode("circle", { cx: 120, cy: 120, r: 86, fill: "none", stroke: color,
            "stroke-width": 18, pathLength: 100, "stroke-dasharray": `${fraction * 100} ${100 - fraction * 100}`,
            "stroke-dashoffset": -offset, transform: "rotate(-90 120 120)", class: "finance-chart-mark" });
          interactive(ring, `${item.label}: ${currency} ${format(item.value)} (${(fraction * 100).toFixed(1)}%)`, detail);
          svg.appendChild(ring);
          offset += fraction * 100;
          const row = document.createElement("div");
          row.className = "finance-legend-row";
          const name = textNode("span", "finance-legend-name", item.label);
          const dot = document.createElement("span");
          dot.className = "finance-legend-dot";
          dot.style.backgroundColor = color;
          name.prepend(dot);
          row.append(name, textNode("strong", "finance-legend-value", format(item.value)),
            textNode("span", "finance-legend-percent", `${(fraction * 100).toFixed(1)}%`));
          legend.appendChild(row);
        });
        svgText(svg, { x: 120, y: 116, "text-anchor": "middle", fill: "#f0f5ff", "font-size": 22, "font-weight": 700 }, compact(sum));
        svgText(svg, { x: 120, y: 139, "text-anchor": "middle", fill: "#9aaac1", "font-size": 10, "letter-spacing": 1.5 }, "TOTAL");
        layout.append(svg, legend);
        wrapper.appendChild(layout);
      } else if (type === "line") {
        const width = Math.max(600, items.length * 52 + 100);
        const svg = svgNode("svg", { viewBox: `0 0 ${width} 260`, role: "group", "aria-label": `Spending trend in ${currency}` });
        svg.style.minWidth = `${width}px`;
        const defs = svgNode("defs");
        const gradient = svgNode("linearGradient", { id: `${id}-${currency}-fill`, x1: "0", x2: "0", y1: "0", y2: "1" });
        gradient.append(svgNode("stop", { offset: "0%", "stop-color": colors[0], "stop-opacity": ".28" }),
          svgNode("stop", { offset: "100%", "stop-color": colors[0], "stop-opacity": "0" }));
        defs.appendChild(gradient);
        svg.appendChild(defs);
        const max = Math.max(...items.map(item => Number(item.value)), 0);
        [0, 0.25, 0.5, 0.75, 1].forEach(part => {
          const y = 200 - part * 160;
          svg.appendChild(svgNode("line", { x1: 62, y1: y, x2: width - 25, y2: y, stroke: "#334058", "stroke-dasharray": "3 7" }));
          svgText(svg, { x: 52, y: y + 4, "text-anchor": "end", fill: "#9aaac1", "font-size": 11 }, compact(max * part));
        });
        const points = items.map((item, index) => ({
          x: items.length === 1 ? width / 2 : 72 + index * (width - 110) / (items.length - 1),
          y: 200 - (max ? Number(item.value) / max : 0) * 160, item,
        }));
        if (points.length > 1) svg.appendChild(svgNode("polygon", {
          points: `${points[0].x},200 ${points.map(point => `${point.x},${point.y}`).join(" ")} ${points[points.length - 1].x},200`,
          fill: `url(#${id}-${currency}-fill)`,
        }));
        svg.appendChild(svgNode("polyline", { points: points.map(point => `${point.x},${point.y}`).join(" "),
          fill: "none", stroke: colors[0], "stroke-width": 3, "stroke-linecap": "round", "stroke-linejoin": "round", class: "finance-trend-line" }));
        points.forEach((point, index) => {
          const circle = svgNode("circle", { cx: point.x, cy: point.y, r: 6, fill: colors[0], stroke: "#101a29", "stroke-width": 3, class: "finance-chart-mark" });
          interactive(circle, `${point.item.label}: ${currency} ${format(point.item.value)}`, detail);
          svg.appendChild(circle);
          if (index === 0 || index === points.length - 1 || index % Math.ceil(points.length / 5) === 0) {
            svgText(svg, { x: point.x, y: 228, "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle", fill: "#9aaac1", "font-size": 11 }, point.item.label);
          }
        });
        const scroll = document.createElement("div");
        scroll.className = "finance-chart-scroll";
        scroll.appendChild(svg);
        wrapper.appendChild(scroll);
      } else {
        const max = Math.max(...items.flatMap(item => [Number(item.value), Number(item.actual || 0)]), 0);
        const bars = document.createElement("div");
        bars.className = "finance-bar-list";
        items.forEach((item, index) => {
          const row = document.createElement("div");
          row.className = "finance-bar-row";
          const header = document.createElement("div");
          header.className = "finance-bar-header";
          header.append(textNode("span", "finance-bar-name", item.label), textNode("strong", "", `${currency} ${format(item.value)}`));
          row.appendChild(header);
          const track = document.createElement("div");
          track.className = "finance-bar-track";
          const bar = document.createElement("div");
          bar.className = "finance-bar-fill";
          bar.style.width = `${max ? Number(item.value) / max * 100 : 0}%`;
          bar.style.setProperty("--bar-color", id === "chart-budget" ? colors[0] : colors[index % colors.length]);
          track.appendChild(bar);
          row.appendChild(track);
          if (item.actual !== null && item.actual !== undefined) {
            const actualHeader = document.createElement("div");
            actualHeader.className = "finance-bar-actual-label";
            actualHeader.append(textNode("span", "", "Recorded actual"), textNode("strong", "", `${currency} ${format(item.actual)}`));
            const actualTrack = document.createElement("div");
            actualTrack.className = "finance-bar-track finance-bar-track-actual";
            const actual = document.createElement("div");
            actual.className = "finance-bar-fill";
            actual.style.width = `${max ? Number(item.actual) / max * 100 : 0}%`;
            actual.style.setProperty("--bar-color", colors[1]);
            actualTrack.appendChild(actual);
            row.append(actualHeader, actualTrack);
          }
          bars.appendChild(row);
        });
        wrapper.appendChild(bars);
      }
      if (type !== "bar") wrapper.appendChild(detail);
      root.appendChild(wrapper);
    });
  }
  chart("chart-distribution", data.distribution || [], "donut");
  chart("chart-trend", data.daily || [], "line");
  chart("chart-materials", data.materials || [], "bar");
  if (document.getElementById("chart-quoted-materials")) chart("chart-quoted-materials", data.quoted_materials || [], "bar");
  chart("chart-costs", data.costs || [], "bar");
  chart("chart-budget", data.budget || [], "bar");
  const project = document.getElementById("finance-project");
  project?.addEventListener("change", () => { window.location.href = project.value; });
  document.querySelectorAll("[data-trend]").forEach(button => {
    button.addEventListener("click", () => {
      document.getElementById("finance-daily").hidden = button.dataset.trend !== "daily";
      document.getElementById("finance-monthly").hidden = button.dataset.trend !== "monthly";
      chart("chart-trend", data[button.dataset.trend] || [], "line");
      document.querySelectorAll("[data-trend]").forEach(item => {
        item.setAttribute("aria-pressed", String(item === button));
      });
    });
  });
  const button = document.getElementById("finance-insights-button");
  button?.addEventListener("click", async () => {
    const result = document.getElementById("finance-insights-result");
    const root = document.getElementById("expenses-module");
    button.disabled = true;
    result.textContent = "Generating insights…";
    try {
      const response = await fetch(root.dataset.insightsUrl + window.location.search, {
        method: "POST",
        headers: { "X-CSRFToken": document.querySelector('meta[name="csrf-token"]').content },
      });
      const data = await response.json();
      result.textContent = response.ok ? `${data.insight} ${data.notice}` : data.error;
      if (response.ok && data.sources.length) {
        const list = document.createElement("ul");
        data.sources.forEach(source => {
          const item = document.createElement("li");
          const link = document.createElement("a");
          link.href = source.url;
          link.textContent = source.name;
          item.appendChild(link);
          list.appendChild(item);
        });
        result.appendChild(list);
      }
    } catch (error) {
      result.textContent = "Could not load insights. Please try again.";
    } finally {
      button.disabled = false;
    }
  });
})();

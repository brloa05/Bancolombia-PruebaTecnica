// Dibuja las figuras Plotly generadas en el servidor (portafolios/graficas.py).
// Cada contenedor [data-grafica] apunta a un <script type="application/json">
// con la figura. En modo oscuro se usan los pasos oscuros de la misma paleta
// (validados contra la superficie oscura) y la tinta/rejilla oscuras.
(function () {
  const PASO_OSCURO = {
    "#2a78d6": "#3987e5",
    "#eb6834": "#d95926",
    "#1baf7a": "#199e70",
    "#eda100": "#c98500",
    "#e87ba4": "#d55181",
  };

  function modoOscuro() {
    const tema = document.documentElement.dataset.theme;
    if (tema) return tema === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function aplicarTema(figura) {
    if (!modoOscuro()) return figura;
    const layout = figura.layout;
    layout.font = { ...layout.font, color: "#c3c2b7" };
    layout.hoverlabel = { ...layout.hoverlabel, bgcolor: "#262625", bordercolor: "#383835", font: { color: "#ffffff" } };
    for (const eje of ["xaxis", "yaxis"]) {
      if (layout[eje]) Object.assign(layout[eje], { gridcolor: "#2c2c2a", zerolinecolor: "#383835", linecolor: "#383835" });
    }
    for (const nota of layout.annotations || []) nota.font = { ...nota.font, color: "#c3c2b7" };
    for (const traza of figura.data) {
      if (traza.marker && PASO_OSCURO[traza.marker.color]) traza.marker.color = PASO_OSCURO[traza.marker.color];
      if (traza.marker && traza.marker.line) traza.marker.line.color = "#1a1a19";
      if (traza.line && PASO_OSCURO[traza.line.color]) traza.line.color = PASO_OSCURO[traza.line.color];
    }
    return figura;
  }

  function dibujar() {
    document.querySelectorAll("[data-grafica]").forEach((contenedor) => {
      const fuente = document.getElementById(contenedor.dataset.grafica);
      if (!fuente) return;
      const figura = aplicarTema(JSON.parse(fuente.textContent));
      Plotly.react(contenedor, figura.data, figura.layout, { displayModeBar: false, responsive: true });
    });
  }

  dibujar();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", dibujar);
})();

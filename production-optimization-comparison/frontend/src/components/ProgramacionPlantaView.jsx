"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import api from "@/lib/api";

const HOUR_W = 46;
const FRAME_DELAY_MS = 550;
const ALGORITHMS = ["CP-SAT", "LNS"];

const sleep = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

const fmt = value =>
  value
    ? new Date(value).toLocaleString("es-PE", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

const fmtNumber = value =>
  value != null ? Number(value).toLocaleString("es-PE") : "—";

const fmtObjective = value =>
  value != null ? `${fmtNumber(value)} pts` : "—";

const toLocalInput = date => {
  const value = new Date(date);
  const pad = number => String(number).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(
    value.getDate(),
  )}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
};

function Kpi({ label, value, sub }) {
  return (
    <div className="kpi">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub}</small>
    </div>
  );
}

function calculateLiveMetrics(rows) {
  const scheduled = rows.filter(order => order.inicio_opt && order.fin_opt);
  if (!scheduled.length) {
    return {
      cumplimiento_pct: 0,
      atraso_total_horas: 0,
      ordenes_retrasadas: 0,
      makespan_horas: 0,
      utilizacion_lineas_pct: 0,
    };
  }

  const onTime = scheduled.filter(
    order => Number(order.atraso_horas || 0) === 0,
  ).length;
  const late = scheduled.filter(
    order => Number(order.atraso_horas || 0) > 0,
  );
  const starts = scheduled.map(order => new Date(order.inicio_opt).getTime());
  const ends = scheduled.map(order => new Date(order.fin_opt).getTime());
  const start = Math.min(...starts);
  const end = Math.max(...ends);
  const makespan = Math.max(0, (end - start) / 3_600_000);
  const processing = scheduled.reduce(
    (total, order) =>
      total +
      (new Date(order.fin_opt) - new Date(order.inicio_opt)) / 3_600_000,
    0,
  );
  const activeLines = new Set(
    scheduled.map(order => order.linea_opt_nombre).filter(Boolean),
  ).size;

  return {
    cumplimiento_pct: Math.round((onTime / scheduled.length) * 1000) / 10,
    atraso_total_horas:
      Math.round(
        late.reduce(
          (total, order) => total + Number(order.atraso_horas || 0),
          0,
        ) * 100,
      ) / 100,
    ordenes_retrasadas: late.length,
    makespan_horas: Math.round(makespan * 100) / 100,
    utilizacion_lineas_pct:
      makespan && activeLines
        ? Math.round((processing / (makespan * activeLines)) * 1000) / 10
        : 0,
  };
}

export default function ProgramacionPlantaView() {
  const [orders, setOrders] = useState([]);
  const [schedulesByAlgorithm, setSchedulesByAlgorithm] = useState({});
  const [metrics, setMetrics] = useState({});
  const [result, setResult] = useState(null);
  const [resultsByAlgorithm, setResultsByAlgorithm] = useState({});
  const [progressByAlgorithm, setProgressByAlgorithm] = useState({});
  const [currentProgress, setCurrentProgress] = useState(null);
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState("SIN EJECUTAR");
  const [error, setError] = useState("");
  const [selectedAlgorithm, setSelectedAlgorithm] = useState("CP-SAT");

  const [config, setConfig] = useState({
    intervalo_minutos: 30,
    tiempo_limite_segundos: 20,
    horizonte_dias: 10,
    setup_formato_horas: 0.5,
    setup_sabor_horas: 0.25,
    limpieza_alergeno_horas: 1,
    porcentaje_destruccion: 0.18,
    temperatura_inicial: 0.05,
    enfriamiento: 0.995,
    semilla: 42,
    max_iteraciones: 2000,
    frecuencia_eventos: 5,
    fecha_inicio_optimizacion: toLocalInput(new Date()),
  });

  const load = useCallback(async () => {
    const [ordersResponse, metricsResponse] = await Promise.all([
      api.get("/ordenes/?page_size=100&ordering=fecha_compromiso"),
      api.get("/indicadores/"),
    ]);
    setOrders(ordersResponse.data.results || ordersResponse.data);
    setMetrics(metricsResponse.data);
  }, []);

  useEffect(() => {
    load().catch(() => setError("No se pudo conectar con el backend."));
  }, [load]);

  const processEvent = async event => {
    if (event.tipo === "started") {
      setPhase(
        event.algoritmo === "COMPARATIVO"
          ? "COMPARANDO CP-SAT Y LNS"
          : `INICIANDO ${event.algoritmo || ""}`,
      );
      return;
    }

    if (event.tipo === "searching") {
      setPhase(`${event.algoritmo || "CP-SAT"} BUSCANDO FACTIBLE`);
      return;
    }

    if (event.tipo === "complete_all") {
      setPhase("COMPARATIVO COMPLETADO");
      return;
    }

    if (event.tipo === "solution") {
      const algorithm =
        event.algoritmo || event.progreso.algoritmo || event.progreso.origen || "CP-SAT";
      setPhase(
        event.progreso.origen === "heuristica"
          ? event.progreso.iteracion === 1
            ? `${algorithm} SOLUCIÓN INICIAL`
            : `${algorithm} MEJORA HEURÍSTICA ${event.progreso.iteracion}`
          : `${algorithm} SOLUCIÓN ${event.progreso.iteracion}`,
      );
      setSchedulesByAlgorithm(previous => ({
        ...previous,
        [algorithm]: event.ordenes,
      }));
      setSelectedAlgorithm(current =>
        schedulesByAlgorithm[current] ? current : algorithm,
      );
      if (algorithm === "CP-SAT" || !schedulesByAlgorithm["CP-SAT"]) {
        setMetrics(calculateLiveMetrics(event.ordenes));
      }
      setCurrentProgress({ ...event.progreso, algoritmo: algorithm });
      setProgressByAlgorithm(previous => ({
        ...previous,
        [algorithm]: [
          ...(previous[algorithm] || []),
          { ...event.progreso, algoritmo: algorithm },
        ],
      }));

      // Permite ver cómo se desplazan las barras y desciende el objetivo.
      await sleep(FRAME_DELAY_MS);
      return;
    }

    if (event.tipo === "complete") {
      const algorithm = event.algoritmo || event.resultado?.algoritmo || "CP-SAT";
      setResult(event.resultado);
      setResultsByAlgorithm(previous => ({
        ...previous,
        [algorithm]: event.resultado,
      }));
      setPhase(`${algorithm} ${event.resultado.estado || "COMPLETADO"}`);
      return;
    }

    if (event.tipo === "error") {
      throw new Error(event.mensaje || "Error durante la optimización.");
    }
  };

  const optimize = async () => {
    setRunning(true);
    setError("");
    setResult(null);
    setResultsByAlgorithm({});
    setSchedulesByAlgorithm({});
    setProgressByAlgorithm({});
    setCurrentProgress(null);
    setSelectedAlgorithm("CP-SAT");
    setPhase("CONECTANDO");

    try {
      const token =
        typeof window !== "undefined"
          ? localStorage.getItem("access_token")
          : null;

      const response = await fetch(
        `${api.defaults.baseURL}/optimizacion/comparar/stream/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            ...config,
            fecha_inicio_optimizacion: new Date().toISOString(),
          }),
        },
      );

      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          detail = JSON.stringify(payload);
        } catch {
          // El cuerpo no necesariamente es JSON.
        }
        throw new Error(detail);
      }

      if (!response.body) {
        throw new Error("El navegador no recibió un flujo de datos.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), {
          stream: !done,
        });

        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";

        for (const block of blocks) {
          const dataLines = block
            .split("\n")
            .filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).trim());

          if (!dataLines.length) continue;

          const event = JSON.parse(dataLines.join("\n"));
          await processEvent(event);
        }

        if (done) break;
      }

      // Garantiza que los indicadores finales provengan de la base de datos.
      await load();
    } catch (exception) {
      setError(exception.message || "Error al optimizar.");
      setPhase("ERROR");
    } finally {
      setRunning(false);
    }
  };

  const reset = async () => {
    if (running) return;
    await api.post("/datos/reiniciar/");
    setResult(null);
    setResultsByAlgorithm({});
    setSchedulesByAlgorithm({});
    setProgressByAlgorithm({});
    setCurrentProgress(null);
    setSelectedAlgorithm("CP-SAT");
    setPhase("SIN EJECUTAR");
    await load();
  };

  const availableAlgorithms = ALGORITHMS.filter(
    algorithm =>
      schedulesByAlgorithm[algorithm]?.length ||
      progressByAlgorithm[algorithm]?.length ||
      resultsByAlgorithm[algorithm],
  );
  const ganttAlgorithms = availableAlgorithms.length
    ? availableAlgorithms
    : ALGORITHMS;
  const activeAlgorithm = ganttAlgorithms.includes(selectedAlgorithm)
    ? selectedAlgorithm
    : ganttAlgorithms[0];

  const visibleRows = schedulesByAlgorithm[activeAlgorithm]?.length
    ? schedulesByAlgorithm[activeAlgorithm].map(order => ({
        ...order,
        algoritmo: activeAlgorithm,
        line_group: order.linea_opt_nombre || "Sin línea",
      }))
    : orders.map(order => ({
        ...order,
        algoritmo: "Actual",
        line_group: order.linea_opt_nombre || "Sin línea",
      }));

  const scheduled = visibleRows.filter(
    order => order.inicio_opt && order.fin_opt,
  );

  const progressChart = useMemo(() => {
    const maxLength = Math.max(
      0,
      ...ALGORITHMS.map(algorithm => (progressByAlgorithm[algorithm] || []).length),
    );
    return Array.from({ length: maxLength }, (_, index) => {
      const row = { iteracion: index + 1 };
      ALGORITHMS.forEach(algorithm => {
        const point = (progressByAlgorithm[algorithm] || [])[index];
        if (point) {
          row[`${algorithm}_objetivo`] = point.objetivo;
          if (point.mejor_cota != null) {
            row[`${algorithm}_cota`] = point.mejor_cota;
          }
        }
      });
      return row;
    });
  }, [progressByAlgorithm]);

  const technicalRows = ALGORITHMS.map(algorithm => {
    const resultForAlgorithm = resultsByAlgorithm[algorithm];
    const history = progressByAlgorithm[algorithm] || [];
    const lastProgress = history[history.length - 1];
    const rows = schedulesByAlgorithm[algorithm] || resultForAlgorithm?.resultado || [];
    const live = rows.length ? calculateLiveMetrics(rows) : {};

    return {
      algorithm,
      estado: resultForAlgorithm?.estado || (history.length ? "EN PROGRESO" : "—"),
      iteraciones: resultForAlgorithm?.iteraciones ?? lastProgress?.iteracion ?? "—",
      objetivo: resultForAlgorithm?.objetivo ?? lastProgress?.objetivo,
      cota: resultForAlgorithm?.mejor_cota ?? lastProgress?.mejor_cota,
      atraso: resultForAlgorithm?.atraso_total_horas ?? lastProgress?.atraso_horas ?? live.atraso_total_horas,
      makespan: resultForAlgorithm?.makespan_horas ?? lastProgress?.makespan_horas ?? live.makespan_horas,
      ordenes: resultForAlgorithm?.ordenes_programadas ?? rows.length,
    };
  });

  const bounds = useMemo(() => {
    const dates = scheduled.flatMap(order => [
      new Date(order.inicio_opt),
      new Date(order.fin_opt),
      new Date(order.fecha_compromiso),
    ]);

    if (!dates.length) {
      const start = new Date();
      start.setMinutes(0, 0, 0);
      return {
        start,
        end: new Date(start.getTime() + 48 * 3_600_000),
      };
    }

    const start = new Date(Math.min(...dates));
    start.setMinutes(0, 0, 0);
    return {
      start,
      end: new Date(Math.max(...dates) + 2 * 3_600_000),
    };
  }, [scheduled]);

  const totalHours = Math.max(
    24,
    Math.ceil((bounds.end - bounds.start) / 3_600_000),
  );

  const lines = [
    ...new Set(
      scheduled.map(order => order.line_group || "Sin línea"),
    ),
  ];

  const lineUtilization = useMemo(() => {
    if (!scheduled.length) return [];
    return lines.map(line => {
      const lineOrders = scheduled.filter(order => (order.line_group || "Sin línea") === line);
      const hours = lineOrders.reduce(
        (sum, order) => sum + (new Date(order.fin_opt) - new Date(order.inicio_opt)) / 3_600_000,
        0,
      );
      return {
        line,
        hours: Math.round(hours * 10) / 10,
        utilization: Math.min(100, Math.round((hours / Math.max(totalHours, 1)) * 1000) / 10),
        orders: lineOrders.length,
      };
    }).sort((a, b) => b.utilization - a.utilization);
  }, [scheduled, lines, totalHours]);

  const completedStages = running
    ? phase.includes("SOLUCIÓN") || phase.includes("MEJORA")
      ? 4
      : phase.includes("BUSCANDO") || phase.includes("COMPARANDO")
        ? 3
        : phase.includes("INICIANDO") || phase.includes("CONECTANDO")
          ? 2
          : 1
    : Object.keys(resultsByAlgorithm).length
      ? 6
      : 0;

  const processStages = [
    ["01", "Datos", `${orders.length} órdenes preparadas`],
    ["02", "Validación", "Parámetros y restricciones"],
    ["03", "Búsqueda", "Exploración de soluciones factibles"],
    ["04", "Asignación", "Órdenes, líneas y secuencias"],
    ["05", "Evaluación", "Objetivo, atraso y capacidad"],
    ["06", "Resultado", "Programa operativo recomendado"],
  ];

  return (
    <main className="plant-programming-page">
      <section className="hero">
        <div className="heroCopy">
          <div className="eyebrowRow">
            <span className="eyebrow">LABORATORIO DE DECISIONES · PRODUCCIÓN</span>
            <span className={`liveBadge ${running ? "isLive" : ""}`}>
              <i /> {running ? "Modelo en ejecución" : "Entorno preparado"}
            </span>
          </div>
          <h1>Una programación que convierte restricciones en ritmo operativo.</h1>
          <p className="lead">
            El algoritmo evalúa las condiciones de planta, compara estrategias de búsqueda y construye una secuencia orientada a reducir atrasos y aprovechar mejor la capacidad disponible.
          </p>
          <div className="heroMeta">
            <span><b>{orders.length}</b> órdenes</span>
            <span><b>{ALGORITHMS.length}</b> modelos comparados</span>
            <span><b>{config.tiempo_limite_segundos}s</b> límite de búsqueda</span>
          </div>
        </div>

        <div className="heroActions">
          <button className="secondary" onClick={reset} disabled={running}>Reiniciar escenario</button>
          <button className="primary" onClick={optimize} disabled={running}>
            {running ? "Optimizando…" : "Ejecutar comparación"}
          </button>
        </div>
      </section>

      {error && <div className="error"><b>No fue posible continuar.</b><span>{error}</span></div>}

      <section className="storyPanel">
        <div className="sectionHeading compact">
          <div>
            <span className="sectionIndex">01 — PROCESO</span>
            <h2>Cómo razona el algoritmo</h2>
          </div>
          <span className="phaseLabel">{phase}</span>
        </div>
        <div className="processRail">
          {processStages.map(([number, title, detail], index) => {
            const active = running && index === Math.min(completedStages, 5);
            const complete = index < completedStages;
            return (
              <article className={`processStep ${complete ? "complete" : ""} ${active ? "active" : ""}`} key={number}>
                <div className="stepMarker">{complete ? "✓" : number}</div>
                <div>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                </div>
              </article>
            );
          })}
        </div>
        {currentProgress && (
          <div className="liveNarrative">
            <span>Observación en vivo</span>
            <p>
              {currentProgress.algoritmo} analiza la iteración <b>#{currentProgress.iteracion || "—"}</b> con un objetivo de <b>{fmtObjective(currentProgress.objetivo)}</b>
              {currentProgress.mejor_cota != null ? <> y una mejor cota de <b>{fmtObjective(currentProgress.mejor_cota)}</b>.</> : "."}
            </p>
          </div>
        )}
      </section>

      <section className="kpis">
        <Kpi label="Cumplimiento" value={`${metrics.cumplimiento_pct || 0}%`} sub="Órdenes terminadas a tiempo" />
        <Kpi label="Atraso acumulado" value={`${metrics.atraso_total_horas || 0} h`} sub={`${metrics.ordenes_retrasadas || 0} órdenes comprometidas`} />
        <Kpi label="Horizonte operativo" value={`${metrics.makespan_horas || 0} h`} sub="Duración total del programa" />
        <Kpi label="Utilización de líneas" value={`${metrics.utilizacion_lineas_pct || 0}%`} sub="Carga sobre recursos activos" />
      </section>

      <section className="workspaceGrid">
        <div className="panel convergencePanel">
          <div className="sectionHeading">
            <div>
              <span className="sectionIndex">02 — APRENDIZAJE</span>
              <h2>Convergencia de las estrategias</h2>
              <p>La curva revela cómo cada método encuentra y refina alternativas.</p>
            </div>
          </div>
          <div className="chart">
            {progressChart.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={progressChart} margin={{ top: 12, right: 16, left: 0, bottom: 4 }}>
                  <CartesianGrid className="chart-grid" stroke="currentColor" strokeDasharray="2 6" vertical={false} />
                  <XAxis dataKey="iteracion" tickLine={false} axisLine={false} tick={{ className: "chart-axis-tick" }} />
                  <YAxis tickLine={false} axisLine={false} width={58} tick={{ className: "chart-axis-tick" }} tickFormatter={value => Number(value).toLocaleString("es-PE", { notation: "compact" })} />
                  <Tooltip formatter={value => fmtObjective(value)} labelFormatter={value => `Iteración ${value}`} wrapperClassName="chart-tooltip" />
                  <Legend iconType="circle" iconSize={7} />
                  <Line className="chart-line chart-line-cpsat" type="monotone" dataKey="CP-SAT_objetivo" name="CP-SAT · objetivo" stroke="currentColor" strokeWidth={3} dot={false} activeDot={{ r: 5 }} connectNulls />
                  <Line className="chart-line chart-line-bound" type="monotone" dataKey="CP-SAT_cota" name="CP-SAT · cota" stroke="currentColor" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls />
                  <Line className="chart-line chart-line-lns" type="monotone" dataKey="LNS_objetivo" name="LNS · objetivo" stroke="currentColor" strokeWidth={3} dot={false} activeDot={{ r: 5 }} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="empty editorialEmpty"><span>La evidencia aparecerá aquí</span><p>Ejecuta el modelo para observar cómo evoluciona la función objetivo.</p></div>}
          </div>
        </div>

        <aside className="panel utilizationPanel">
          <div className="sectionHeading">
            <div>
              <span className="sectionIndex">03 — CAPACIDAD</span>
              <h2>Lectura de recursos</h2>
              <p>Ocupación estimada por línea activa.</p>
            </div>
          </div>
          <div className="utilizationList">
            {lineUtilization.length ? lineUtilization.slice(0, 6).map(item => (
              <div className="utilizationItem" key={item.line}>
                <div><strong>{item.line}</strong><span>{item.orders} órdenes · {item.hours} h</span></div>
                <div className="meter"><i style={{ width: `${item.utilization}%` }} /></div>
                <b>{item.utilization}%</b>
              </div>
            )) : <div className="empty smallEmpty">Sin asignaciones calculadas.</div>}
          </div>
        </aside>
      </section>

      <section className="panel ganttPanel">
        <div className="sectionHeading ganttHeading">
          <div>
            <span className="sectionIndex">04 — PROGRAMA RESULTANTE</span>
            <h2>Secuencia de producción</h2>
            <p>Cada barra representa una decisión temporal; la marca roja indica la fecha compromiso.</p>
          </div>
          <div className="panelActions">
            <div className="segmented">
              {ganttAlgorithms.map(algorithm => (
                <button key={algorithm} type="button" className={activeAlgorithm === algorithm ? "active" : ""} onClick={() => setSelectedAlgorithm(algorithm)}>{algorithm}</button>
              ))}
            </div>
          </div>
        </div>

        {!scheduled.length ? <div className="empty editorialEmpty"><span>Aún no existe un programa</span><p>La línea de tiempo se construirá con cada solución factible.</p></div> : (
          <div className="ganttScroll">
            <div style={{ minWidth: 280 + totalHours * HOUR_W }}>
              <div className="ganttHeader">
                <div className="fixed">Línea / orden</div>
                <div className="timeline" style={{ width: totalHours * HOUR_W }}>
                  {Array.from({ length: totalHours }, (_, index) => {
                    const date = new Date(bounds.start.getTime() + index * 3_600_000);
                    return <div key={index} style={{ width: HOUR_W }}><b>{date.getHours().toString().padStart(2, "0")}</b><small>{date.getHours() === 0 ? date.toLocaleDateString("es-PE", { day: "2-digit", month: "short" }) : ""}</small></div>;
                  })}
                </div>
              </div>
              {lines.map(line => (
                <div key={line}>
                  <div className="lineName"><span>{line}</span><small>{scheduled.filter(order => (order.line_group || "Sin línea") === line).length} operaciones</small></div>
                  {scheduled.filter(order => (order.line_group || "Sin línea") === line).sort((a, b) => new Date(a.inicio_opt) - new Date(b.inicio_opt)).map(order => {
                    const x = ((new Date(order.inicio_opt) - bounds.start) / 3_600_000) * HOUR_W;
                    const width = Math.max(12, ((new Date(order.fin_opt) - new Date(order.inicio_opt)) / 3_600_000) * HOUR_W);
                    const due = ((new Date(order.fecha_compromiso) - bounds.start) / 3_600_000) * HOUR_W;
                    return (
                      <div className="ganttRow" key={`${order.algoritmo}-${order.id}`}>
                        <div className="fixed"><b>{order.numero}</b><small>{order.sku_nombre} · {Number(order.cantidad).toLocaleString()} u.</small></div>
                        <div className="track" style={{ width: totalHours * HOUR_W }}>
                          <div className={`bar ${Number(order.atraso_horas) > 0 ? "late" : ""}`} style={{ transform: `translateX(${x}px)`, width }} title={`${fmt(order.inicio_opt)} – ${fmt(order.fin_opt)}`}><span>{order.numero}</span></div>
                          {due >= 0 && due <= totalHours * HOUR_W && <div className="due" style={{ transform: `translateX(${due}px)` }} />}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="bottomGrid">
        <div className="panel technicalPanel">
          <div className="sectionHeading">
            <div><span className="sectionIndex">05 — COMPARACIÓN</span><h2>Resultado técnico</h2><p>Una lectura breve para decidir qué modelo llevar a operación.</p></div>
          </div>
          <div className="technicalList">
            {technicalRows.map(row => (
              <article className={`technicalCard ${activeAlgorithm === row.algorithm ? "selected" : ""}`} key={row.algorithm} onClick={() => setSelectedAlgorithm(row.algorithm)}>
                <div className="technicalHeader"><div><span>MODELO</span><strong>{row.algorithm}</strong></div><em>{row.estado}</em></div>
                <dl><dt>Objetivo</dt><dd>{fmtObjective(row.objetivo)}</dd><dt>Iteraciones</dt><dd>{row.iteraciones}</dd><dt>Atraso</dt><dd>{row.atraso != null ? `${row.atraso} h` : "—"}</dd><dt>Makespan</dt><dd>{row.makespan != null ? `${row.makespan} h` : "—"}</dd><dt>Órdenes</dt><dd>{row.ordenes || "—"}</dd><dt>Mejor cota</dt><dd>{row.cota != null ? fmtObjective(row.cota) : row.algorithm === "LNS" ? "No aplica" : "—"}</dd></dl>
              </article>
            ))}
          </div>
        </div>

        <aside className="panel settingsPanel">
          <div className="sectionHeading"><div><span className="sectionIndex">CONFIGURACIÓN</span><h2>Condiciones del experimento</h2><p>Parámetros visibles, sin apartar la atención del resultado.</p></div></div>
          <div className="controls">
            <label><span>Intervalo de decisión</span><select value={config.intervalo_minutos} onChange={event => setConfig({ ...config, intervalo_minutos: Number(event.target.value) })}><option value={15}>15 minutos</option><option value={30}>30 minutos</option><option value={60}>60 minutos</option></select></label>
            <label><span>Límite del solver</span><div className="inputUnit"><input type="number" min="1" max="300" value={config.tiempo_limite_segundos} onChange={event => setConfig({ ...config, tiempo_limite_segundos: Number(event.target.value) })} /><b>seg</b></div></label>
          </div>
          <div className="editorialNote">“La calidad de una programación no se mide solo por su precisión matemática, sino por la claridad con la que puede convertirse en una decisión.”</div>
        </aside>
      </section>

    </main>
  );
}
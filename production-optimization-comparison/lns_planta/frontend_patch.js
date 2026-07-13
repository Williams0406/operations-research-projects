// En ProgramacionPlantaView.jsx usa:
const LNS_STREAM_URL = `${API_URL}/optimizacion/lns/stream/`;

// Agrega al estado config:
const lnsConfig = {
  porcentaje_destruccion: 0.25,
  temperatura_inicial: 0.05,
  enfriamiento: 0.995,
  semilla: 42,
  max_iteraciones: 500,
};

// El lector SSE actual no necesita cambios: los eventos conservan
// { tipo, progreso, ordenes } y el evento final { tipo: "complete", resultado }.

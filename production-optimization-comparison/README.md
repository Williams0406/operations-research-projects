# Planificador de producción alimentaria

Proyecto demostrativo completo con **Django REST Framework + SQLite + OR-Tools CP-SAT + Next.js + Recharts**.

## Funcionalidad

- Datos sintéticos de SKUs, líneas, turnos, operarios y órdenes.
- Compatibilidad SKU–línea y velocidad específica.
- Restricciones de liberación, fecha compromiso, no solapamiento y personal.
- Setups dependientes de formato, sabor y transición desde producto con alérgeno.
- Gantt por línea.
- Historial de soluciones del solver.
- KPIs: cumplimiento, atraso, makespan y utilización.

## 1. Backend

Requiere Python 3.11 o 3.12.

### Windows PowerShell

```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo --ordenes 18
python manage.py runserver
```

### macOS / Linux

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo --ordenes 18
python manage.py runserver
```

Backend: `http://127.0.0.1:8000/api/`

## 2. Frontend

Requiere Node.js 20 o superior. En una segunda terminal:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

En PowerShell, reemplaza `cp` por:

```powershell
Copy-Item .env.example .env.local
```

Frontend: `http://localhost:3000`

## 3. Flujo de demostración

1. Abre el frontend.
2. Verifica que existan órdenes cargadas.
3. Pulsa **Ejecutar optimización**.
4. Revisa el Gantt, KPIs y evolución de la función objetivo.
5. Usa **Reiniciar** para borrar únicamente la programación calculada.

## API principal

- `GET /api/ordenes/`
- `GET /api/indicadores/`
- `POST /api/optimizacion/ejecutar/`
- `POST /api/datos/reiniciar/`

## Nota de alcance

Es una demostración de portafolio con datos completamente sintéticos. El límite de 40 órdenes protege el tiempo de respuesta del modelo de setups pareados. Para escala industrial conviene usar intervalos de mayor granularidad, descomposición por horizonte o una formulación de circuito/secuencia más especializada.

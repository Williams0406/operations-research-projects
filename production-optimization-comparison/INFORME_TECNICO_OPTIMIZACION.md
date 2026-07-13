# Informe técnico de optimización de programación de planta

## 1. Contexto del proyecto

Este proyecto implementa un planificador de producción para una planta alimentaria con múltiples líneas, productos, turnos, operarios y órdenes de fabricación. El objetivo es convertir una cartera de órdenes pendientes en un programa de producción visualizable, medible y comparable desde una interfaz web.

Desde la perspectiva de ingeniería industrial, el problema corresponde a un caso de **programación de operaciones en líneas paralelas con restricciones de compatibilidad, fechas de liberación, fechas compromiso, setups dependientes de secuencia y capacidad operativa**. Es decir, no basta con ordenar las órdenes por fecha de entrega: cada SKU solo puede fabricarse en ciertas líneas, cada línea tiene una velocidad distinta, algunas transiciones de producto generan tiempos de preparación y las órdenes tienen ventanas temporales específicas.

Desde la perspectiva de ciencia de datos, la aplicación permite comparar dos enfoques de optimización:

- **CP-SAT**, un modelo exacto/constraint programming basado en OR-Tools.
- **LNS**, una metaheurística de búsqueda por grandes vecindarios, diseñada para explorar rápidamente alternativas de programación.

La interfaz muestra el avance de ambos modelos en tiempo real, el resultado técnico de cada algoritmo, una curva de convergencia y un Gantt para inspeccionar la solución generada por cada enfoque.

## 2. Datos del modelo

El sistema trabaja con datos sintéticos generados por `seed_demo`, orientados a representar una planta alimentaria con productos como salsas, aderezos y conservas. Estos datos son suficientes para demostrar fenómenos reales de planificación: asignación de línea, atrasos, setups, utilización y comparación de algoritmos.

### 2.1 SKUs

Cada SKU representa un producto fabricable. Sus atributos principales son:

- **Código**: identificador del producto.
- **Nombre**: descripción comercial.
- **Familia**: salsa, aderezo o conserva.
- **Formato**: botella, doypack, frasco, etc.
- **Sabor**: atributo usado para calcular cambios de preparación.
- **Contiene alérgeno**: indicador relevante para limpieza adicional.
- **Unidad**: unidad de producción.

Estos atributos no son decorativos: se usan para calcular compatibilidad y tiempos de setup entre productos consecutivos.

### 2.2 Líneas de producción

Las líneas representan recursos productivos paralelos. Cada línea tiene:

- **Código y nombre**.
- **Estado activo/inactivo**.
- **Head count** requerido.
- **Capacidad por hora**.

La capacidad de línea afecta la duración estimada de una orden. A mayor velocidad o capacidad, menor duración productiva.

### 2.3 Compatibilidad SKU-línea

No todos los SKUs pueden fabricarse en todas las líneas. La tabla de compatibilidad define:

- Qué SKU puede correr en qué línea.
- Cuál es la velocidad específica de ese SKU en esa línea.

Esta relación es central para la optimización porque convierte el problema en una decisión de asignación: el algoritmo debe decidir no solo cuándo producir una orden, sino también en qué línea conviene producirla.

### 2.4 Turnos y operarios

Los turnos representan disponibilidad de personal por franja horaria. Los operarios pertenecen a turnos y pueden estar activos o inactivos.

En corridas pequeñas, el modelo puede considerar restricciones detalladas de personal por slot. En corridas más grandes, la evaluación de personal se aligera para que los algoritmos puedan generar soluciones en tiempo razonable. Esta decisión es una práctica habitual en modelos de planificación: se balancea fidelidad contra velocidad computacional.

### 2.5 Órdenes de fabricación

Cada orden contiene:

- **Número de orden**.
- **SKU**.
- **Cantidad**.
- **Prioridad**.
- **Fecha de liberación**.
- **Fecha compromiso**.
- **Línea e inicio/fin optimizados**.
- **Atraso en horas**.
- **Estado**: pendiente, programada, retrasada, no factible, etc.

La fecha de liberación indica desde cuándo puede producirse la orden. La fecha compromiso indica cuándo debería estar terminada. La diferencia entre ambas define la ventana disponible para programar.

## 3. Naturaleza del problema de optimización

El problema puede resumirse como:

> Asignar cada orden a una línea compatible y a un intervalo de tiempo, respetando restricciones operativas y minimizando penalidades de atraso, uso de recursos y duración total del plan.

### 3.1 Restricciones principales

El modelo considera las siguientes restricciones:

- Una orden solo puede asignarse a líneas compatibles con su SKU.
- Una orden no puede comenzar antes de su fecha de liberación.
- Dos órdenes no pueden solaparse en la misma línea.
- La duración depende de la cantidad y de la velocidad SKU-línea.
- Los setups dependen de la transición entre productos consecutivos.
- El atraso se calcula cuando el fin optimizado supera la fecha compromiso.
- En escenarios pequeños, también se consideran restricciones de personal.

### 3.2 Setups dependientes de secuencia

Los setups representan tiempos improductivos entre órdenes. El sistema considera tres fuentes:

- Cambio de formato.
- Cambio de sabor.
- Limpieza adicional cuando se pasa de un producto con alérgeno a uno sin alérgeno.

Esto es industrialmente importante porque dos programas con las mismas órdenes pueden tener productividad muy distinta según el orden en que se ejecuten.

## 4. Función objetivo

Tanto CP-SAT como LNS usan una función objetivo basada en penalidades. El valor mostrado en la interfaz como **Objetivo (pts)** es un puntaje de costo, no una magnitud física directa. Menor es mejor.

La estructura general es:

```text
Objetivo =
  penalidad por atraso ponderado
  + penalidad por personal extra
  + penalidad por setups
  + penalidad por makespan
```

En CP-SAT, el objetivo principal pondera fuertemente el atraso:

```text
W_ATRASO * atraso_ponderado
+ W_EXTRA * personal_extra
+ W_MAKESPAN * makespan
```

En LNS se incluye además una penalidad explícita por setup:

```text
W_ATRASO * atraso_ponderado
+ W_EXTRA * personal_extra
+ W_SETUP * setup_total
+ W_MAKESPAN * makespan
```

La escala de puntos existe para que el algoritmo priorice lo más importante. Por ejemplo, el atraso pesa mucho más que pequeñas variaciones de makespan, porque en una planta real incumplir compromisos de entrega suele ser más costoso que extender ligeramente la duración total del plan.

## 5. Modelo CP-SAT

CP-SAT es un enfoque de programación por restricciones y optimización combinatoria. En este proyecto se implementa con OR-Tools.

### 5.1 Qué decide CP-SAT

CP-SAT decide:

- Qué línea asignar a cada orden.
- En qué slot iniciar cada orden.
- En qué slot finalizarla.
- Qué órdenes quedan retrasadas.
- Cómo secuenciar órdenes en cada línea.
- Cómo minimizar el costo total del programa.

### 5.2 Variables principales

El modelo usa variables de:

- **Inicio**: slot en el que empieza la orden.
- **Fin**: slot en el que termina la orden.
- **Presencia**: variable booleana para indicar si una alternativa SKU-línea fue elegida.
- **Intervalos opcionales**: usados para evitar solapamientos en líneas.
- **Atraso**: diferencia positiva entre fin y fecha compromiso.
- **Makespan**: fin más tardío del programa.

### 5.3 Ventajas de CP-SAT

CP-SAT tiene ventajas importantes:

- Produce soluciones con fundamento matemático.
- Puede entregar una **mejor cota**, útil para saber qué tan lejos está la solución de un límite teórico.
- Genera múltiples soluciones factibles durante la búsqueda.
- Es muy bueno para restricciones duras como no solapamiento, asignación y fechas.

### 5.4 Limitaciones de CP-SAT

También tiene costos:

- Puede crecer mucho en tamaño cuando hay muchas órdenes y granularidad fina.
- Las restricciones de secuencia y setups elevan la complejidad combinatoria.
- Si el tiempo límite es bajo, puede terminar con una solución factible pero no óptima.

Por eso el proyecto usa callbacks para mostrar el avance del solver en vivo. Cada solución factible encontrada se envía al frontend y actualiza el Gantt, el gráfico de convergencia y el resultado técnico.

## 6. Modelo LNS

LNS significa **Large Neighborhood Search**. Es una metaheurística: no intenta probar matemáticamente la optimalidad, sino explorar inteligentemente el espacio de soluciones para encontrar buenas programaciones en poco tiempo.

### 6.1 Idea general

LNS parte de una solución inicial y repite un ciclo:

1. Destruir una parte de la solución.
2. Repararla insertando nuevamente las órdenes removidas.
3. Evaluar si la nueva solución es mejor.
4. Aceptar o rechazar la solución según criterios de mejora y temperatura.
5. Repetir hasta agotar tiempo o iteraciones.

Este enfoque es muy usado en planificación industrial porque permite explorar soluciones grandes sin formular todo el problema como un modelo exacto pesado.

### 6.2 Solución inicial

El LNS implementado usa multi-arranque. Construye soluciones iniciales con diferentes estrategias:

- **due**: prioriza fecha compromiso.
- **priority**: prioriza órdenes de mayor prioridad.
- **release**: prioriza disponibilidad temprana.
- **largest**: prioriza órdenes grandes.
- **slack**: prioriza menor holgura entre liberación y compromiso.

Luego conserva la mejor solución inicial según la función objetivo.

### 6.3 Operadores de destrucción

El LNS usa varios operadores:

- **random**: remueve órdenes aleatorias para diversificar.
- **late**: remueve las órdenes con mayor atraso.
- **line**: remueve un bloque de órdenes de una línea.
- **setup**: remueve órdenes asociadas a cambios costosos.
- **mixed**: combina órdenes atrasadas con aleatoriedad.

El operador mixto es útil porque balancea explotación y exploración: ataca el atraso, pero evita quedar atrapado siempre en la misma zona.

### 6.4 Reparación

Una vez removidas las órdenes, el algoritmo intenta reinsertarlas en posiciones convenientes. Para mejorar rendimiento en corridas grandes, la reparación usa muestreo de posiciones. Esto evita revisar exhaustivamente todas las posiciones posibles cuando el problema crece.

### 6.5 Mejora local

El LNS también aplica una mejora local por reinserción de órdenes atrasadas. La idea es simple y potente: si una orden genera mucho atraso, se prueba moverla a una ubicación mejor.

### 6.6 Ventajas de LNS

LNS aporta:

- Velocidad y flexibilidad.
- Muchas iteraciones en poco tiempo.
- Buen comportamiento en problemas grandes.
- Capacidad de generar soluciones aun cuando el modelo exacto se vuelve pesado.
- Fácil incorporación de heurísticas de negocio.

### 6.7 Limitaciones de LNS

LNS no entrega una cota matemática de optimalidad. Por eso en la interfaz aparece:

```text
Mejor cota: No aplica
```

Esto no significa que LNS sea inferior; significa que pertenece a otra familia metodológica. LNS busca buenas soluciones, pero no demuestra formalmente cuán lejos está del óptimo global.

## 7. Comparación CP-SAT vs LNS

| Aspecto | CP-SAT | LNS |
|---|---|---|
| Tipo de enfoque | Optimización exacta / restricciones | Metaheurística |
| Garantía matemática | Puede entregar cota | No entrega cota |
| Velocidad en problemas pequeños | Alta | Alta |
| Escalabilidad en problemas grandes | Puede degradarse | Suele escalar mejor |
| Interpretabilidad de optimalidad | Mayor | Menor |
| Flexibilidad heurística | Media | Alta |
| Mejor uso | Soluciones con garantías y restricciones duras | Exploración rápida y escenarios grandes |

En una aplicación industrial madura, ambos enfoques pueden convivir:

- CP-SAT sirve como referencia matemática y solución robusta para instancias manejables.
- LNS sirve como motor práctico para explorar escenarios, reaccionar rápido y generar buenas soluciones en ventanas de tiempo cortas.

## 8. Datos mostrados en Resultado técnico

El panel **Resultado técnico** resume el estado final o actual de cada modelo.

### 8.1 Estado

Indica la situación del algoritmo.

Ejemplos:

- **FACTIBLE**: CP-SAT encontró una solución válida.
- **OPTIMO**: CP-SAT probó optimalidad.
- **HEURISTICA**: CP-SAT no cerró solución propia y se conservó una solución heurística.
- **LNS_FINALIZADO**: LNS terminó su búsqueda.
- **EN PROGRESO**: el modelo aún está transmitiendo soluciones.

### 8.2 Objetivo

Se muestra como:

```text
Objetivo: X pts
```

Es el puntaje de penalidad total. No representa horas, unidades ni dinero directamente. Es una combinación ponderada de atraso, personal extra, setups y makespan.

Regla de lectura:

```text
Menor objetivo = mejor solución según los pesos definidos.
```

### 8.3 Iteraciones

En CP-SAT, representa las soluciones factibles capturadas por el callback o el avance registrado.

En LNS, representa el número de ciclos de destrucción/reparación ejecutados.

Una mayor cantidad de iteraciones no garantiza automáticamente mejor solución, pero sí indica más exploración del espacio de búsqueda.

### 8.4 Atraso

Se expresa en horas:

```text
Atraso: X h
```

Representa la suma de retrasos de las órdenes programadas. Una orden aporta atraso si:

```text
fin_optimizado > fecha_compromiso
```

Este indicador es crítico en contextos industriales porque se relaciona con cumplimiento de servicio, penalidades comerciales y confiabilidad del plan maestro.

### 8.5 Makespan

Se expresa en horas:

```text
Makespan: X h
```

Representa la duración total del programa, desde el primer inicio hasta el último fin considerado en la solución.

Un makespan menor implica que el conjunto de órdenes se completa antes, pero no siempre es el criterio más importante. En muchos casos conviene aceptar un makespan ligeramente mayor si reduce atrasos de órdenes prioritarias.

### 8.6 Órdenes

Indica cuántas órdenes fueron programadas por el modelo.

Si el número es menor al total esperado, puede indicar órdenes no factibles por compatibilidad, horizonte o restricciones.

### 8.7 Mejor cota

En CP-SAT, la mejor cota representa un límite matemático calculado por el solver. Sirve para evaluar la brecha entre la solución actual y un posible óptimo.

En términos simples:

```text
Si objetivo y cota están cerca, la solución está cerca del óptimo probado.
```

En LNS aparece:

```text
No aplica
```

porque LNS no calcula una cota global de optimalidad.

## 9. Gráfico de convergencia

El gráfico **Convergencia de los modelos** muestra cómo evoluciona el objetivo durante la búsqueda.

Las series principales son:

- **CP-SAT objetivo (pts)**: valor de la mejor solución factible encontrada por CP-SAT.
- **CP-SAT cota (pts)**: mejor límite matemático reportado por CP-SAT.
- **LNS objetivo (pts)**: valor de la solución generada por LNS durante sus iteraciones.

La lectura esperada es:

- Una curva descendente indica mejora.
- Saltos fuertes indican descubrimiento de una programación sustancialmente mejor.
- Mesetas indican que el algoritmo está explorando sin encontrar mejoras relevantes.
- CP-SAT puede mostrar cota; LNS no.

## 10. Gantt de programación

El Gantt muestra el calendario resultante de cada modelo. La interfaz permite alternar entre:

- **CP-SAT**
- **LNS**

Cada barra representa una orden programada en una línea. La posición horizontal indica inicio y fin. La línea vertical de fecha compromiso permite observar si una orden termina antes o después del compromiso.

Una barra tardía indica que la orden tiene atraso. Este análisis visual complementa el objetivo numérico: dos soluciones pueden tener objetivo parecido, pero distribuciones de carga y atrasos muy distintas.

## 11. Análisis técnico e industrial

El problema de programación de planta es inherentemente combinatorio. Con pocas órdenes, el número de secuencias posibles ya crece rápidamente. Si además se consideran líneas alternativas, setups y ventanas temporales, el espacio de búsqueda se vuelve muy grande.

El enfoque comparativo implementado es adecuado porque no depende de un único algoritmo. En la práctica industrial, usar un solo método puede ser riesgoso:

- Un modelo exacto puede tardar demasiado en instancias grandes.
- Una heurística puede encontrar buenas soluciones, pero no probar optimalidad.
- Un plan operativo necesita tanto calidad como velocidad.

La combinación CP-SAT + LNS permite cubrir ambos frentes.

### 11.1 Valor de CP-SAT

CP-SAT aporta disciplina matemática. Es especialmente útil cuando el negocio necesita justificar que una solución respeta restricciones duras y se aproxima al óptimo. También es muy valioso para instancias pequeñas o medianas donde puede generar múltiples soluciones factibles y cotas.

### 11.2 Valor de LNS

LNS aporta flexibilidad y velocidad. Es útil para escenarios donde se requiere una respuesta rápida, por ejemplo:

- Reprogramación durante el turno.
- Cambios urgentes de prioridad.
- Fallas de línea.
- Inserción de órdenes no planificadas.
- Comparación rápida de escenarios.

### 11.3 Interpretación de resultados

Si CP-SAT y LNS llegan a resultados similares, se gana confianza en la solución: dos métodos distintos convergen hacia un plan comparable.

Si CP-SAT supera a LNS, puede indicar que las heurísticas necesitan más tiempo o ajustes.

Si LNS supera a CP-SAT dentro del mismo límite de tiempo, puede indicar que el modelo exacto necesita más tiempo, menor granularidad o simplificación de restricciones.

## 12. Recomendaciones de uso

Para análisis exploratorio:

- Usar intervalo de 60 minutos si se quiere velocidad.
- Usar intervalo de 30 minutos si se quiere más precisión.
- Aumentar el límite del solver cuando se busque mejor calidad.

Para demostración ejecutiva:

- Generar 20 a 30 órdenes.
- Usar fechas compromiso con holgura amplia.
- Mostrar primero la convergencia.
- Luego comparar el Gantt CP-SAT vs LNS.

Para mayor realismo industrial:

- Incorporar calendarios de mantenimiento.
- Agregar tiempos de limpieza por familia y alérgeno con datos reales.
- Diferenciar líneas por disponibilidad horaria.
- Incorporar costos monetarios reales para atraso, setup y horas extra.
- Separar órdenes firmes de órdenes pronosticadas.

## 13. Conclusión

El proyecto demuestra una arquitectura sólida para planificación de producción con enfoque analítico. Combina datos operativos, API transaccional, modelos de optimización y visualización en tiempo real. Desde ingeniería industrial, captura elementos clave del problema: compatibilidad, capacidad, secuencia, setups, fechas compromiso y atrasos. Desde ciencia de datos, permite comparar enfoques exactos y heurísticos, observar convergencia y tomar decisiones basadas en métricas.

CP-SAT entrega rigor matemático y capacidad de evaluar cotas. LNS entrega velocidad, flexibilidad y buena capacidad de exploración. La combinación de ambos modelos es especialmente valiosa porque permite contrastar soluciones y construir confianza en el plan resultante.

El panel **Resultado técnico**, la curva de **Convergencia de los modelos** y el **Gantt** cumplen roles complementarios:

- El resultado técnico resume calidad y estado.
- La convergencia muestra el proceso de búsqueda.
- El Gantt traduce la solución a una lectura operativa.

En conjunto, la aplicación no solo genera un programa de producción; también permite explicar por qué una solución es buena, cómo fue encontrada y qué diferencias existen entre modelos de optimización alternativos.

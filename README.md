# API Aire Puno

Servicio independiente para AirPredict. Consulta el pronóstico global de PM2.5 de CAMS mediante Open-Meteo para la celda más cercana a la ciudad de Puno y devuelve una estimación con formato compatible con la respuesta de la API general de AirPredict.

## Importante sobre los datos

- No existen mediciones de una estación local de Puno en esta API.
- Los valores proceden del pronóstico global CAMS, con resolución aproximada de 45 km y resolución temporal de origen de 3 horas.
- El pronóstico global se actualiza cada 12 horas; la caché de esta API dura 15 minutos por defecto.
- La fecha de referencia del contrato de AirPredict contiene el tiempo válido del punto CAMS más cercano, no una observación local.
- Esto es un punto de partida reproducible para integrar una estimación externa. No es un modelo calibrado con datos de Puno ni debe presentarse como medición local.

Open-Meteo indica que su API gratuita permite uso no comercial y educativo, con atribución CC BY 4.0 y límites de uso. Verifique sus términos antes de un despliegue de producción.

## Endpoints

- GET /health: estado del servicio.
- GET /api/puno/estimate: pronóstico para Puno usando las claves compatibles con la respuesta general de AirPredict: mode, is_current_forecast, site_id, model_version, units, predicted_pm25, observed_date, target_date, input_origin, outside_training_period y note.
- GET /api/puno/forecast: serie horaria de pronóstico y metadatos de origen.

FastAPI sirve Swagger en /docs y OpenAPI en /openapi.json.

## Ejecutar localmente

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8001

Prueba manual:

    curl http://127.0.0.1:8001/api/puno/estimate

## Conectar AirPredict

Configurar el cliente de AirPredict para consultar GET https://<url-del-servicio>/api/puno/estimate. El servidor de AirPredict no debe asumir que observed_date es una medición: comprobar input_origin y mostrar la nota de procedencia. AIRPREDICT_ORIGINS controla los dominios de navegador autorizados por CORS.

## Despliegue y DevOps

La carpeta está preparada para ser un servicio web Docker independiente en Render. Para mantenerlo externo a AirPredict, publíquela en un repositorio GitHub propio y cree un servicio Render conectado a ese repositorio. Configure el health check como /health. Render puede ejecutar el Dockerfile de la raíz.

El render.yaml deja desactivado el auto despliegue para poder conectar un GitHub Actions workflow propio con validación, construcción y un Render Deploy Hook. No se incluye un flujo que publique automáticamente hasta crear el repositorio remoto y configurar los secretos RENDER_DEPLOY_HOOK y PUNO_API_BASE_URL.

No se reentrena un modelo con predicciones generadas por esta misma API: eso sería retroalimentación circular y no validación. La fuente CAMS se consulta al solicitar el dato y se reutiliza en memoria durante 15 minutos.

## Compatibilidad con la API general de AirPredict

POST /api/general/predict acepta el mismo cuerpo que la API general:

    {
      "site_id": "puno_city",
      "observed_date": "AAAA-MM-DD",
      "pm25": 12.5,
      "previous_pm25": 11.8
    }

Para esta ruta, observed_date debe ser la fecha actual de Perú y site_id debe ser puno_city o puno. Devuelve las mismas claves de respuesta que la predicción general de AirPredict. El valor de entrada es un escenario proporcionado por el usuario; la API añade el cambio de PM2.5 previsto por CAMS para las siguientes 24 horas. previous_pm25 se acepta por compatibilidad, pero no se usa en ese ajuste.

Para incorporar el pronóstico externo automático, AirPredict debe consultar GET /api/puno/estimate. Si el navegador hace la llamada directamente, configurar AIRPREDICT_ORIGINS con el dominio público de AirPredict. Si el servidor de AirPredict hace la llamada, CORS no aplica.
# ApiAirePuno

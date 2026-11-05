# Mejoras pagas a futuro (no implementadas)

Ideas para cuando el volumen lo justifique. **Nada de esto está hecho**: los costos son
estimados y hay que confirmarlos con la tarifa vigente de cada proveedor antes de contratar.

| Mejora | Para qué | Costo estimado | Riesgos / notas |
|---|---|---|---|
| **VPS chico** (Hetzner, DigitalOcean, Contabo) | Tener la app online 24 h, compartida con los socios, y correr la búsqueda automática aunque la PC esté apagada | ~USD 5–10/mes | Hay que sumar login (hoy no tiene) y HTTPS. Backups pasan a ser responsabilidad del servidor |
| **Evolution API** (self-hosted en el VPS) | Mandar WhatsApp desde el sistema y leer respuestas automáticamente | Gratis el software; usa el VPS de arriba | **No es oficial**: usa WhatsApp Web por detrás. Mandar en frío puede hacer que **bloqueen el número**. Usarla solo con quien ya respondió |
| **WhatsApp Cloud API (oficial, Meta)** | Envíos con plantillas aprobadas y sin riesgo de bloqueo | Se paga por conversación según categoría y país (consultar tarifa vigente de Meta) | Para iniciar conversaciones exige plantilla aprobada y **consentimiento previo**: no sirve para prospección en frío |
| **Supabase compartido** | Una sola base para los 3 socios, con usuarios y permisos | Plan gratis (500 MB, se pausa tras 1 semana sin uso) o Pro ~USD 25/mes | La capa `sales/repository.py` y SQLAlchemy permiten migrar cambiando `DATABASE_URL` a Postgres y corriendo las migraciones |
| **Gemini con facturación** | Más cupo de IA (analizar cientos de leads por día) | Por uso, centavos de dólar por cada cientos de mensajes (ver precios de Google AI) | Hoy alcanza el cupo gratis; si se activa facturación, poner tope de presupuesto |
| **Google Places por encima del cupo gratis** | Buscar más de ~1.000 páginas de resultados por mes | ~USD 35 cada 1.000 consultas extra (Text Search Enterprise) | Mantener cuotas por día en Google Cloud para no tener sorpresas |
| **Google Calendar** | Agendar reuniones desde la ficha | Gratis (API con cuota) | Requiere OAuth con tu cuenta; quedó como opcional para la fase final |

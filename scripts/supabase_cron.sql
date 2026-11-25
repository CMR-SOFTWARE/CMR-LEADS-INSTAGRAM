-- Tareas automáticas de CMR Leads en la nube (pg_cron + pg_net de Supabase).
-- Correr en el SQL Editor del proyecto "CMR Landing Page + CMR Nexo", DESPUÉS del deploy en Vercel.
--
-- Antes de correrlo:
--   1. Reemplazá TU-DOMINIO por el dominio de Vercel (ej: cmr-leads.vercel.app), sin https://.
--   2. Guardá la clave de las tareas en Vault (la misma que pusiste en CRON_SECRET en Vercel).
--      Corré esta línea sola, con tu clave, y no la guardes en ningún archivo:
--        select vault.create_secret('LA-CLAVE', 'cmr_leads_cron_secret');

create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.cmr_leads_tarea(nombre text)
returns void
language sql
security definer
set search_path = public
as $$
  select net.http_post(
    url := 'https://TU-DOMINIO/tareas/' || nombre,
    headers := jsonb_build_object(
      'Authorization',
      'Bearer ' || (select decrypted_secret from vault.decrypted_secrets
                    where name = 'cmr_leads_cron_secret')
    ),
    timeout_milliseconds := 300000
  );
$$;

revoke all on function public.cmr_leads_tarea(text) from public, anon, authenticated;

-- Horarios en UTC (Argentina = UTC-3).
select cron.schedule('cmr-leads-publicar',    '* * * * *',    $$select public.cmr_leads_tarea('publicar')$$);
select cron.schedule('cmr-leads-historias',   '5 * * * *',    $$select public.cmr_leads_tarea('historias')$$);
select cron.schedule('cmr-leads-estadisticas','20 */6 * * *', $$select public.cmr_leads_tarea('estadisticas')$$);
select cron.schedule('cmr-leads-publicidad',  '*/30 * * * *', $$select public.cmr_leads_tarea('publicidad')$$);
select cron.schedule('cmr-leads-mensajes',    '*/15 * * * *', $$select public.cmr_leads_tarea('mensajes')$$);
select cron.schedule('cmr-leads-busqueda',    '0 12 * * *',   $$select public.cmr_leads_tarea('busqueda')$$);

-- Ver cómo salieron las últimas llamadas:
--   select status_code, content, created from net._http_response order by created desc limit 20;
-- Apagar una tarea:
--   select cron.unschedule('cmr-leads-busqueda');

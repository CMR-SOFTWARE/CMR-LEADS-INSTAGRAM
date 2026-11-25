-- Tareas automáticas de CMR Leads en la nube (pg_cron + pg_net de Supabase).
-- YA ESTÁ APLICADO en el proyecto "CMR Landing Page + CMR Nexo". Queda como referencia.
--
-- Cómo funciona:
--   - La clave CRON_SECRET se generó adentro de Supabase y está en Vault ("cmr_leads_cron_secret").
--     Para copiarla a Vercel, en el SQL Editor:
--       select decrypted_secret from vault.decrypted_secrets where name = 'cmr_leads_cron_secret';
--   - El dominio de Vercel también va en Vault ("cmr_leads_url"). Mientras no esté, las tareas no
--     hacen nada. Para cargarlo (una sola vez):
--       select vault.create_secret('https://TU-DOMINIO.vercel.app', 'cmr_leads_url');

create extension if not exists pg_cron;
create extension if not exists pg_net;

select vault.create_secret(encode(extensions.gen_random_bytes(32), 'hex'), 'cmr_leads_cron_secret',
  'Clave CRON_SECRET de CMR Leads (va igual en Vercel)');

create or replace function cmr_leads.tarea(nombre text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  base text := (select decrypted_secret from vault.decrypted_secrets where name = 'cmr_leads_url');
  clave text := (select decrypted_secret from vault.decrypted_secrets where name = 'cmr_leads_cron_secret');
begin
  if base is null or clave is null then
    return; -- todavía no hay dominio de Vercel cargado
  end if;
  perform net.http_post(
    url := rtrim(base, '/') || '/tareas/' || nombre,
    headers := jsonb_build_object('Authorization', 'Bearer ' || clave),
    timeout_milliseconds := 300000
  );
end;
$$;
revoke all on function cmr_leads.tarea(text) from public, anon, authenticated;

-- Horarios en UTC (Argentina = UTC-3).
select cron.schedule('cmr-leads-publicar',     '* * * * *',    $$select cmr_leads.tarea('publicar')$$);
select cron.schedule('cmr-leads-historias',    '5 * * * *',    $$select cmr_leads.tarea('historias')$$);
select cron.schedule('cmr-leads-estadisticas', '20 */6 * * *', $$select cmr_leads.tarea('estadisticas')$$);
select cron.schedule('cmr-leads-publicidad',   '*/30 * * * *', $$select cmr_leads.tarea('publicidad')$$);
select cron.schedule('cmr-leads-mensajes',     '*/15 * * * *', $$select cmr_leads.tarea('mensajes')$$);
select cron.schedule('cmr-leads-busqueda',     '0 12 * * *',   $$select cmr_leads.tarea('busqueda')$$);

-- Ver cómo salieron las últimas llamadas:
--   select status_code, content, created from net._http_response order by created desc limit 20;
-- Apagar una tarea:
--   select cron.unschedule('cmr-leads-busqueda');

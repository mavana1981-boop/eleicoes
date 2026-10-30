const $ = id => document.getElementById(id);
const fmt = n => Number(n || 0).toLocaleString("pt-BR");
const pct = (a, b) => b ? (a / b * 100).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + "%" : "–";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const UFS = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA","PB","PE","PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO","ZZ"];
const CHUNK = 8 * 1024 * 1024;

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  let body = null;
  try { body = await r.json(); } catch { /* sem corpo */ }
  if (!r.ok) throw new Error(body?.erro || `Erro ${r.status}`);
  return body;
}
function msg(el, texto, erro = false) { el.textContent = texto; el.classList.toggle("err", erro); }

// ------------------------------------------------ estados
$("ufs").innerHTML = UFS.map(u => `<label class="chip"><input type="checkbox" name="uf" value="${u}">${u}</label>`).join("");
$("marcarTodas").onclick = () => document.querySelectorAll("[name=uf]").forEach(c => c.checked = true);
$("limparUfs").onclick = () => document.querySelectorAll("[name=uf]").forEach(c => c.checked = false);

// ------------------------------------------------ resumo
async function carregarResumo() {
  const {por_turno, por_uf} = await api("/api/resumo");
  const colunas = [...new Set(por_turno.map(r => r.coluna))];
  const principal = colunas[0];
  const turnos = [...new Set(por_turno.map(r => r.turno))].sort();
  if (!turnos.length) {
    $("totals").innerHTML = `<p class="empty">Nenhum dado carregado ainda. Use uma das opções abaixo para buscar os boletins.</p>`;
    $("tabUf").innerHTML = ""; $("atualizado").textContent = ""; return;
  }
  $("totals").innerHTML = turnos.map(t => {
    const linhas = por_turno.filter(r => r.turno === t);
    const p = linhas.find(r => r.coluna === principal) || linhas[0];
    return `<div class="turno">
      <span class="eyebrow">${t}º turno · ${p.ufs} de 28 UFs</span>
      <span class="big">${fmt(p.valor)}</span>
      <span class="small">${pct(p.valor, p.comparecimento)} do comparecimento · ${fmt(p.secoes)} seções · ${fmt(p.comparecimento)} votantes</span>
      ${linhas.filter(r => r !== p).map(r => `<span class="small mono">${esc(r.coluna)}: ${fmt(r.valor)}</span>`).join("")}
      ${p.ufs < 28 ? `<span class="small">Faltam ${28 - p.ufs} UFs para o total nacional.</span>` : ""}
    </div>`;
  }).join("");
  const datas = por_uf.map(r => r.atualizado).sort();
  $("atualizado").textContent = datas.length ? "Atualizado em " + new Date(datas.at(-1)).toLocaleString("pt-BR") : "";

  // tabela por UF (coluna principal)
  const rows = por_uf.filter(r => r.coluna === principal);
  const tot = {};
  rows.forEach(r => { const t = tot[r.turno] ??= {s: 0, c: 0, v: 0}; t.s += r.secoes; t.c += r.comparecimento; t.v += r.valor; });
  $("tabUf").innerHTML = `<thead><tr><th class="l">Turno</th><th class="l">UF</th><th>Seções</th><th>Comparecimento</th><th>Sem biometria</th><th>%</th></tr></thead>
    <tbody>${rows.map(r => `<tr class="click" data-t="${r.turno}" data-uf="${r.uf}" tabindex="0">
      <td class="l">${r.turno}º</td><td class="l">${r.uf}</td><td>${fmt(r.secoes)}</td><td>${fmt(r.comparecimento)}</td><td>${fmt(r.valor)}</td><td>${pct(r.valor, r.comparecimento)}</td></tr>`).join("")}</tbody>
    <tfoot>${Object.entries(tot).map(([t, x]) => `<tr><td class="l">${t}º</td><td class="l">Total</td><td>${fmt(x.s)}</td><td>${fmt(x.c)}</td><td>${fmt(x.v)}</td><td>${pct(x.v, x.c)}</td></tr>`).join("")}</tfoot>`;
  $("tabUf").dataset.col = principal;
}

$("tabUf").addEventListener("click", e => abrirMun(e.target.closest("tr.click")));
$("tabUf").addEventListener("keydown", e => { if (e.key === "Enter") abrirMun(e.target.closest("tr.click")); });
async function abrirMun(tr) {
  if (!tr) return;
  const {t, uf} = tr.dataset, col = $("tabUf").dataset.col;
  const linhas = (await api(`/api/municipios?turno=${t}&uf=${uf}`)).filter(r => r.coluna === col);
  $("titMun").textContent = `Municípios · ${uf} · ${t}º turno`;
  $("tabMun").innerHTML = `<thead><tr><th class="l">Município</th><th>Seções</th><th>Comparecimento</th><th>Sem biometria</th><th>%</th></tr></thead>
    <tbody>${linhas.map(r => `<tr><td class="l">${esc(r.nm_municipio || r.cd_municipio)}</td><td>${fmt(r.secoes)}</td><td>${fmt(r.comparecimento)}</td><td>${fmt(r.valor)}</td><td>${pct(r.valor, r.comparecimento)}</td></tr>`).join("")}</tbody>`;
  $("secMun").hidden = false;
  $("secMun").scrollIntoView({behavior: "smooth"});
}
$("fecharMun").onclick = () => $("secMun").hidden = true;

// ------------------------------------------------ tarefas
let ativo = false, ultimoEstado = "";
async function carregarTarefas() {
  const ts = await api("/api/tarefas");
  ativo = ts.some(t => t.status === "executando" || t.status === "na fila");
  $("jobs").innerHTML = ts.length ? ts.map(t => `<li class="job">
      <span class="desc">${esc(t.descricao)}</span><span class="pill ${esc(t.status)}">${esc(t.status)}</span>
      ${t.status === "executando" ? `<div class="bar"><i style="width:${(t.progresso * 100).toFixed(1)}%"></i></div>` : ""}
      <span class="info">${esc(t.mensagem)}</span></li>`).join("") : `<li class="empty">Nenhuma tarefa ainda.</li>`;
  const estado = ts.map(t => t.id + t.status).join();
  if (estado !== ultimoEstado) { ultimoEstado = estado; carregarResumo(); }
}
async function ciclo() {
  try { await carregarTarefas(); } catch (e) { console.error(e); }
  setTimeout(ciclo, ativo ? 2000 : 8000);
}

// ------------------------------------------------ buscar no TSE
$("formTse").addEventListener("submit", async e => {
  e.preventDefault();
  const turnos = [...document.querySelectorAll("[name=turno]:checked")].map(c => c.value);
  const ufs = [...document.querySelectorAll("[name=uf]:checked")].map(c => c.value);
  if (!turnos.length) return msg($("msgTse"), "Marque ao menos um turno.", true);
  $("btnTse").disabled = true;
  try {
    await api("/api/tse", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({turnos, ufs})});
    msg($("msgTse"), "Tarefa criada. Acompanhe em Tarefas.");
    ativo = true; carregarTarefas();
  } catch (err) { msg($("msgTse"), err.message, true); }
  finally { $("btnTse").disabled = false; }
});

// ------------------------------------------------ envio em partes
const inp = $("arquivo"), drop = $("drop");
inp.addEventListener("change", () => escolher(inp.files[0]));
["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => escolher(e.dataTransfer.files[0]));
let arquivo = null;
function escolher(f) {
  if (!f) return;
  if (!/\.(zip|csv)$/i.test(f.name)) { msg($("msgUp"), "Escolha um arquivo .zip ou .csv.", true); return; }
  arquivo = f;
  $("dropTxt").textContent = `${f.name} (${(f.size / 1e6).toLocaleString("pt-BR", {maximumFractionDigits: 0})} MB)`;
  $("btnUp").disabled = false; msg($("msgUp"), "");
}

$("formUp").addEventListener("submit", async e => {
  e.preventDefault();
  if (!arquivo) return;
  $("btnUp").disabled = true; $("upBarBox").hidden = false;
  try {
    const {id} = await api("/api/upload/init", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({nome: arquivo.name, tamanho: arquivo.size})});
    const partes = Math.ceil(arquivo.size / CHUNK);
    const t0 = performance.now();
    for (let i = 0; i < partes; i++) {
      const blob = arquivo.slice(i * CHUNK, Math.min((i + 1) * CHUNK, arquivo.size));
      for (let tentativa = 1; ; tentativa++) {
        try { await api(`/api/upload/${id}/${i}`, {method: "PUT", body: blob}); break; }
        catch (err) {
          if (tentativa >= 5) throw err;
          msg($("msgUp"), `Falha na parte ${i + 1}, tentando de novo (${tentativa}/5)…`, true);
          await new Promise(r => setTimeout(r, 1500 * tentativa));
        }
      }
      const feito = (i + 1) / partes, seg = (performance.now() - t0) / 1000;
      const resta = seg / feito * (1 - feito);
      $("upBar").style.width = (feito * 100) + "%";
      msg($("msgUp"), `Enviando ${(feito * 100).toFixed(0)}% · faltam ~${Math.ceil(resta / 60)} min`);
    }
    await api(`/api/upload/${id}/fim`, {method: "POST"});
    msg($("msgUp"), "Arquivo recebido. O processamento aparece em Tarefas.");
    ativo = true; carregarTarefas();
  } catch (err) { msg($("msgUp"), err.message, true); }
  finally { $("btnUp").disabled = false; }
});

// ------------------------------------------------ apagar
$("btnLimpar").onclick = () => $("confLimpar").hidden = false;
$("naoLimpar").onclick = () => $("confLimpar").hidden = true;
$("simLimpar").onclick = async () => { await api("/api/limpar", {method: "POST"}); $("confLimpar").hidden = true; carregarResumo(); };

// ------------------------------------------------ impressão / PDF
function carimbarData() { $("geradoEm").textContent = "Relatório gerado em " + new Date().toLocaleString("pt-BR"); }
window.addEventListener("beforeprint", carimbarData);
$("btnImprimir").onclick = () => { carimbarData(); window.print(); };

carregarResumo().catch(console.error);
ciclo();

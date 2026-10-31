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

// ------------------------------------------------ rodada em exibição
let rodadaAtual = null;   // null = resultados atuais; senão {id, criado, descricao}
const qs = (extra = "") => {
  const p = new URLSearchParams(extra);
  if (rodadaAtual) p.set("rodada", rodadaAtual.id);
  const t = p.toString();
  return t ? "?" + t : "";
};
const dataBR = iso => new Date(iso).toLocaleString("pt-BR");

function verRodada(r) {
  rodadaAtual = r;
  $("banner").hidden = !r;
  if (r) $("bannerTxt").textContent = `Você está vendo a rodada #${r.id} de ${dataBR(r.criado)} · ${r.descricao}`;
  $("expUf").href = "/api/export.csv" + qs("nivel=uf");
  $("expMun").href = "/api/export.csv" + qs("nivel=municipio");
  $("expLogs").href = "/api/logs/export.csv" + qs();
  $("secLogSec").hidden = true;
  carregarLogs();
  $("secMun").hidden = true;
  carregarResumo();
  carregarRodadas();
  window.scrollTo({top: 0, behavior: "smooth"});
}
$("voltarAtual").onclick = () => verRodada(null);

// ------------------------------------------------ resumo
async function carregarResumo() {
  const {por_turno, por_uf} = await api("/api/resumo" + qs());
  const colunas = [...new Set(por_turno.map(r => r.coluna))];
  const principal = colunas[0];
  const turnos = [...new Set(por_turno.map(r => r.turno))].sort();
  if (!turnos.length) {
    $("totals").innerHTML = `<p class="empty">${rodadaAtual ? "Esta rodada não tem dados." : "Nenhum dado carregado ainda. Use uma das opções abaixo para buscar os boletins."}</p>`;
    $("porUf").innerHTML = ""; $("atualizado").textContent = ""; return;
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
  $("atualizado").textContent = datas.length ? (rodadaAtual ? `Rodada #${rodadaAtual.id} · ` : "Atualizado em ") + dataBR(datas.at(-1)) : "";

  // uma tabela por turno (coluna principal)
  $("porUf").innerHTML = turnos.map(t => {
    const rows = por_uf.filter(r => r.coluna === principal && r.turno === t);
    const x = rows.reduce((a, r) => ({s: a.s + r.secoes, c: a.c + r.comparecimento, v: a.v + r.valor}), {s: 0, c: 0, v: 0});
    return `<div class="bloco-turno">
      <h3>${t}º turno</h3>
      <div class="tablebox"><table data-col="${esc(principal)}">
        <thead><tr><th class="l">UF</th><th>Seções</th><th>Comparecimento</th><th>Sem biometria</th><th>%</th></tr></thead>
        <tbody>${rows.map(r => `<tr class="click" data-t="${r.turno}" data-uf="${r.uf}" tabindex="0">
          <td class="l">${r.uf}</td><td>${fmt(r.secoes)}</td><td>${fmt(r.comparecimento)}</td><td>${fmt(r.valor)}</td><td>${pct(r.valor, r.comparecimento)}</td></tr>`).join("")}</tbody>
        <tfoot><tr><td class="l">Total ${t}º turno</td><td>${fmt(x.s)}</td><td>${fmt(x.c)}</td><td>${fmt(x.v)}</td><td>${pct(x.v, x.c)}</td></tr></tfoot>
      </table></div>
    </div>`;
  }).join("");
}

$("porUf").addEventListener("click", e => abrirMun(e.target.closest("tr.click")));
$("porUf").addEventListener("keydown", e => { if (e.key === "Enter") abrirMun(e.target.closest("tr.click")); });
async function abrirMun(tr) {
  if (!tr) return;
  const {t, uf} = tr.dataset, col = tr.closest("table").dataset.col;
  const linhas = (await api("/api/municipios" + qs(`turno=${t}&uf=${uf}`))).filter(r => r.coluna === col);
  $("titMun").textContent = `Municípios · ${uf} · ${t}º turno` + (rodadaAtual ? ` · rodada #${rodadaAtual.id}` : "");
  $("tabMun").innerHTML = `<thead><tr><th class="l">Município</th><th>Seções</th><th>Comparecimento</th><th>Sem biometria</th><th>%</th></tr></thead>
    <tbody>${linhas.map(r => `<tr><td class="l">${esc(r.nm_municipio || r.cd_municipio)}</td><td>${fmt(r.secoes)}</td><td>${fmt(r.comparecimento)}</td><td>${fmt(r.valor)}</td><td>${pct(r.valor, r.comparecimento)}</td></tr>`).join("")}</tbody>`;
  $("secMun").hidden = false;
  $("secMun").scrollIntoView({behavior: "smooth"});
}
$("fecharMun").onclick = () => $("secMun").hidden = true;

// ------------------------------------------------ rodadas arquivadas
let rodadasCache = [];
async function carregarRodadas() {
  rodadasCache = await api("/api/rodadas");
  $("rodadas").innerHTML = rodadasCache.length ? rodadasCache.map(r => {
    const cols = [...new Set(r.totais.map(t => t.coluna))];
    const resumo = [
      ...r.totais.filter(t => t.coluna === cols[0])
        .map(t => `BU ${t.turno}º turno: ${fmt(t.valor)} sem biometria (${pct(t.valor, t.comparecimento)}, ${t.ufs} UFs)`),
      ...(r.logs || []).map(t => `Logs ${t.turno}º turno: ${fmt(t.sem_apos)} sem digital após 17h, ${fmt(t.votos_apos)} votos após 17h (${fmt(t.secoes)} seções)`),
    ].join(" · ");
    return `<li class="rodada${rodadaAtual?.id === r.id ? " ativa" : ""}" data-id="${r.id}">
      <span class="desc">#${r.id} · ${dataBR(r.criado)} · ${esc(r.descricao)}</span>
      <span class="info">${resumo || "sem dados"}</span>
      <span class="acoes">
        <button type="button" class="btn ghost" data-acao="ver">${rodadaAtual?.id === r.id ? "Em exibição" : "Ver"}</button>
        <button type="button" class="link danger" data-acao="excluir">Excluir</button>
        <span data-conf hidden>Excluir mesmo? <button type="button" class="link danger" data-acao="sim">Sim</button> · <button type="button" class="link" data-acao="nao">Não</button></span>
      </span></li>`;
  }).join("") : `<li class="empty">Nenhuma rodada arquivada ainda.</li>`;
}
$("rodadas").addEventListener("click", async e => {
  const b = e.target.closest("[data-acao]"); if (!b) return;
  const li = b.closest(".rodada"), id = Number(li.dataset.id);
  const conf = li.querySelector("[data-conf]");
  const acao = b.dataset.acao;
  if (acao === "ver") verRodada(rodadasCache.find(r => r.id === id));
  else if (acao === "excluir") conf.hidden = false;
  else if (acao === "nao") conf.hidden = true;
  else if (acao === "sim") {
    await api(`/api/rodadas/${id}`, {method: "DELETE"});
    if (rodadaAtual?.id === id) verRodada(null); else carregarRodadas();
  }
});

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
  if (estado !== ultimoEstado) { ultimoEstado = estado; carregarResumo(); carregarRodadas(); carregarLogs(); carregarDiag(); }
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
  if (!/\.(zip|csv|logjez)$/i.test(f.name)) { msg($("msgUp"), "Escolha um arquivo .zip, .csv ou .logjez.", true); return; }
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
      body: JSON.stringify({nome: arquivo.name, tamanho: arquivo.size, turno: $("upTurno").value, uf: $("upUf").value})});
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
function carimbarData() {
  $("modoPrint").textContent = "Horário de corte: " + (modoHora() === "aj" ? "17h de Brasília, com ajuste do fuso de cada UF." : "17h no relógio da urna, sem ajuste de fuso.");
  $("geradoEm").textContent = "Relatório gerado em " + new Date().toLocaleString("pt-BR") +
    (rodadaAtual ? ` · Rodada #${rodadaAtual.id} de ${dataBR(rodadaAtual.criado)} (${rodadaAtual.descricao})` : " · Resultados atuais");
}
window.addEventListener("beforeprint", carimbarData);
$("btnImprimir").onclick = () => { carimbarData(); window.print(); };

// ------------------------------------------------ logs de urna: votos após as 17h
const hm = v => v || "–";
const modoHora = () => document.querySelector("[name=modo]:checked")?.value || "aj";
document.querySelectorAll(".sel-uf").forEach(sel => sel.insertAdjacentHTML("beforeend", UFS.map(u => `<option value="${u}">${u}</option>`).join("")));
document.querySelectorAll("[name=modo]").forEach(r => r.addEventListener("change", () => { $("secLogSec").hidden = true; renderLogs(); }));

let logsCache = [];
async function carregarLogs() {
  const {por_uf} = await api("/api/logs/resumo" + qs());
  logsCache = por_uf;
  renderLogs();
}
function renderLogs() {
  const m = modoHora(), rows = logsCache;
  const turnos = [...new Set(rows.map(r => r.turno))].sort();
  if (!turnos.length) {
    $("logTotals").innerHTML = `<p class="empty">${rodadaAtual ? "Esta rodada não tem logs." : "Nenhum log processado ainda. Envie um ZIP de logs em \"Enviar arquivo\" ou informe o endereço do arquivo no TSE abaixo."}</p>`;
    $("logPorUf").innerHTML = ""; return;
  }
  const soma = (lst, k) => lst.reduce((a, r) => a + (r[k] || 0), 0);
  $("logTotals").innerHTML = turnos.map(t => {
    const l = rows.filter(r => r.turno === t);
    const va = soma(l, "votos_apos_" + m), sa = soma(l, "sem_apos_" + m), v = soma(l, "votos"), sem = soma(l, "sem");
    const zeroSem = v > 0 && sem === 0, zeroVoto = v === 0;
    return `<div class="turno">
      <span class="eyebrow">${t}º turno · logs de ${fmt(soma(l, "secoes"))} seções em ${l.length} UF(s)</span>
      <span class="small">Liberados sem digital com voto após as 17h</span>
      <span class="big">${fmt(sa)}</span>
      <div class="linha"><span>Votos computados após as 17h</span><span>${fmt(va)}</span></div>
      <div class="linha"><span>Sem digital entre os votos após 17h</span><span>${pct(sa, va)}</span></div>
      <div class="linha"><span>Votos computados no dia (logs)</span><span>${fmt(v)}</span></div>
      <div class="linha"><span>Liberados sem digital no dia (logs)</span><span>${fmt(sem)}</span></div>
      ${zeroVoto ? `<span class="alerta">Nenhum voto computado foi reconhecido nos logs. Confira os padrões de mensagem.</span>` : ""}
      ${zeroSem ? `<span class="alerta">Nenhuma liberação sem digital foi reconhecida. Confira os padrões de mensagem.</span>` : ""}
    </div>`;
  }).join("");

  $("logPorUf").innerHTML = turnos.map(t => {
    const l = rows.filter(r => r.turno === t);
    return `<div class="bloco-turno">
      <h3>${t}º turno</h3>
      <div class="tablebox"><table>
        <thead><tr><th class="l">UF</th><th>Seções (logs)</th><th>Votos</th><th>Votos após 17h</th><th>Sem digital</th><th>Sem digital após 17h</th>
          <th>1º voto*</th><th>Último voto*</th><th>BU sem biometria</th><th>Seções no BU</th></tr></thead>
        <tbody>${l.map(r => `<tr class="click" data-t="${r.turno}" data-uf="${r.uf}" tabindex="0">
          <td class="l">${r.uf}${r.fuso ? ` <span class="small">(${r.fuso > 0 ? "+" : ""}${r.fuso}h)</span>` : ""}</td>
          <td>${fmt(r.secoes)}</td><td>${fmt(r.votos)}</td><td>${fmt(r["votos_apos_" + m])}</td><td>${fmt(r.sem)}</td>
          <td><strong>${fmt(r["sem_apos_" + m])}</strong></td><td>${hm(r.primeiro_mediana)}</td><td>${hm(r.ultimo_mediana)}</td>
          <td>${r.bu_sem == null ? "–" : fmt(r.bu_sem)}</td><td>${r.bu_secoes == null ? "–" : fmt(r.bu_secoes)}</td></tr>`).join("")}</tbody>
        <tfoot><tr><td class="l">Total</td><td>${fmt(soma(l, "secoes"))}</td><td>${fmt(soma(l, "votos"))}</td><td>${fmt(soma(l, "votos_apos_" + m))}</td>
          <td>${fmt(soma(l, "sem"))}</td><td>${fmt(soma(l, "sem_apos_" + m))}</td><td></td><td></td><td></td><td></td></tr></tfoot>
      </table></div>
    </div>`;
  }).join("") + `<p class="small">* Mediana entre as seções, pela hora gravada na urna. Se o 1º voto do Acre aparece por volta das 06:00, o relógio da urna está na hora local e o corte "17h de Brasília" é o correto. "Sem digital" nos logs deve ficar próximo de "BU sem biometria" quando todas as seções da UF foram processadas; uma diferença grande indica que os padrões de mensagem precisam de ajuste.</p>`;
}

$("logPorUf").addEventListener("click", e => abrirLogSec(e.target.closest("tr.click")));
$("logPorUf").addEventListener("keydown", e => { if (e.key === "Enter") abrirLogSec(e.target.closest("tr.click")); });
async function abrirLogSec(tr) {
  if (!tr) return;
  const {t, uf} = tr.dataset;
  const linhas = await api("/api/logs/secoes" + qs(`turno=${t}&uf=${uf}&modo=${modoHora()}`));
  $("titLogSec").textContent = `Seções · ${uf} · ${t}º turno` + (rodadaAtual ? ` · rodada #${rodadaAtual.id}` : "") + " · até 300, ordenadas por sem digital após 17h";
  $("tabLogSec").innerHTML = `<thead><tr><th class="l">Município</th><th>Zona</th><th>Seção</th><th>Votos</th><th>Votos após 17h</th><th>Sem digital</th><th>Sem digital após 17h</th><th>1º voto</th><th>Último voto</th></tr></thead>
    <tbody>${linhas.map(r => `<tr><td class="l">${esc(r.nm_municipio || r.cd_municipio || "–")}</td><td>${esc(r.zona)}</td><td>${esc(r.secao)}</td>
      <td>${fmt(r.votos)}</td><td>${fmt(r.votos_apos)}</td><td>${fmt(r.sem)}</td><td><strong>${fmt(r.sem_apos)}</strong></td><td>${hm(r.primeiro)}</td><td>${hm(r.ultimo)}</td></tr>`).join("")}</tbody>`;
  $("secLogSec").hidden = false;
  $("secLogSec").scrollIntoView({behavior: "smooth"});
}
$("fecharLogSec").onclick = () => $("secLogSec").hidden = true;

$("formLogUrl").addEventListener("submit", async e => {
  e.preventDefault();
  $("btnLogUrl").disabled = true;
  try {
    await api("/api/logs/url", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url: $("logUrl").value, turno: $("logTurno").value, uf: $("logUf").value})});
    msg($("msgLogUrl"), "Tarefa criada. Acompanhe em Tarefas.");
    ativo = true; carregarTarefas();
  } catch (err) { msg($("msgLogUrl"), err.message, true); }
  finally { $("btnLogUrl").disabled = false; }
});

async function carregarPadroes() {
  const {padroes} = await api("/api/logs/config");
  $("padIni").value = padroes.inicio; $("padSem").value = padroes.sem_digital; $("padComp").value = padroes.computado;
}
$("formPadroes").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/api/logs/config", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({padroes: {inicio: $("padIni").value, sem_digital: $("padSem").value, computado: $("padComp").value}})});
    msg($("msgPad"), "Padrões salvos. Processe os logs de novo para aplicar."); carregarDiag();
  } catch (err) { msg($("msgPad"), err.message, true); }
});
$("restaurarPad").onclick = async () => {
  await api("/api/logs/config", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({restaurar: true})});
  await carregarPadroes(); msg($("msgPad"), "Padrões originais restaurados."); carregarDiag();
};

const ROTULO_PAD = {inicio: "início", sem_digital: "sem digital", computado: "voto computado"};
async function carregarDiag() {
  const d = await api("/api/logs/diagnostico");
  if (!d) { $("diagInfo").textContent = "(nenhum log processado)"; $("tabDiag").innerHTML = ""; return; }
  const {mensagens, logs: n, erros} = d.diagnostico;
  const pad = {inicio: $("padIni").value, sem_digital: $("padSem").value, computado: $("padComp").value};
  const marca = txt => Object.entries(pad).filter(([, rx]) => { try { return new RegExp(rx).test(txt); } catch { return false; } }).map(([k]) => k);
  $("diagInfo").textContent = `· ${d.descricao} · ${n} logs · amostra dos primeiros 30` + (erros?.length ? ` · ${erros.length} erro(s) de leitura` : "");
  $("tabDiag").innerHTML = `<thead><tr><th class="l">Marcação (padrões atuais)</th><th class="l">Mensagem (números trocados por #)</th><th>Ocorrências</th></tr></thead>
    <tbody>${mensagens.map(([txt, c]) => `<tr><td class="l">${marca(txt).map(k => `<span class="tag">${ROTULO_PAD[k]}</span>`).join("") || "–"}</td>
      <td class="msgcol mono">${esc(txt)}</td><td>${fmt(c)}</td></tr>`).join("")}</tbody>`;
}

carregarResumo().catch(console.error);
carregarRodadas().catch(console.error);
carregarLogs().catch(console.error);
carregarPadroes().then(carregarDiag).catch(console.error);
ciclo();

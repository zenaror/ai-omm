const $ = (id) => document.getElementById(id);
const state = { data: null, timer: null, reviewing: false };
const typeLabels = {fact:"Fato",observation:"Observação",hypothesis:"Hipótese",unknown:"Dúvida",decision:"Decisão",conclusion:"Conclusão",constraint:"Regra",contract:"Contrato"};
function el(tag, cls, text) { const node=document.createElement(tag); if(cls) node.className=cls; if(text!==undefined) node.textContent=text; return node; }
function size(bytes) { if(bytes<1024) return `${bytes} B`; if(bytes<1048576) return `${(bytes/1024).toFixed(1)} KB`; return `${(bytes/1048576).toFixed(2)} MB`; }
function toast(message) { const node=$("toast"); node.textContent=message; node.classList.add("visible"); setTimeout(()=>node.classList.remove("visible"),2600); }
function addOption(select,value,label) { const option=el("option",null,label); option.value=value; select.append(option); }
function skillScopeLabel(scope){if(scope==="cross-project-domain")return "Compartilhada entre projetos";if(scope.startsWith("project:"))return "Específica deste projeto";if(scope==="global")return "Todos os projetos";return scope;}
function skillInheritanceLabel(item){const labels=[item.inherited?"Herdada":"Alcance: "+skillScopeLabel(item.scope)];if(item.inherited_by?.length)labels.push("Herdada por: "+item.inherited_by.join(" · "));if(item.inherits?.length)labels.push("Herda de: "+item.inherits.join(" → "));return labels.join(" · ");}
const workspaceTabs = ["memory", "review", "followup"];
function selectWorkspaceTab(name, focus = false) {
  if (!workspaceTabs.includes(name)) return;
  for (const tab of workspaceTabs) {
    const selected = tab === name, button = $("tab-" + tab);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $("view-" + tab).hidden = !selected;
  }
  if (focus) $("tab-" + name).focus();
}
for (const [index, name] of workspaceTabs.entries()) {
  const button = $("tab-" + name);
  button.addEventListener("click", () => selectWorkspaceTab(name));
  button.addEventListener("keydown", event => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % workspaceTabs.length;
    if (event.key === "ArrowLeft") next = (index + workspaceTabs.length - 1) % workspaceTabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = workspaceTabs.length - 1;
    if (next !== undefined) { event.preventDefault(); selectWorkspaceTab(workspaceTabs[next], true); }
  });
}
function openQuestionsFromLink() {
  if (location.hash === "#open-questions") {
    selectWorkspaceTab("followup");
    $("open-questions").scrollIntoView({block: "start"});
  }
}
window.addEventListener("hashchange", openQuestionsFromLink);
document.querySelector('a[href="#open-questions"]').addEventListener("click", () => selectWorkspaceTab("followup"));
openQuestionsFromLink();
function render(data) {
  state.data=data; const s=data.summary;
  $("active-count").textContent=s.active_count; $("unknown-count").textContent=s.unknown_count;
  $("memory-size").textContent=size(s.memory_bytes+s.skill_bytes+(s.source_bytes||0)); $("skill-count").textContent=s.skill_count;
  const indexTime=s.index_updated_at?new Date(s.index_updated_at).toLocaleString("pt-BR"):"não criado";
  const backupTime=s.backup_commit_at?new Date(s.backup_commit_at).toLocaleString("pt-BR"):"nenhum commit encontrado";
  const freshness=s.index_current?"índice parece atualizado":"índice pode precisar ser reconstruído";
  $("health").textContent=`${freshness} (${indexTime}) · último commit dos dados: ${backupTime} · índice local: ${size(s.index_bytes||0)}`;
  const scopeSelect=$("scope"), old=scopeSelect.value; scopeSelect.replaceChildren(); addOption(scopeSelect,"","Todos os projetos");
  for(const scope of data.scopes) addOption(scopeSelect,scope,scope==="global"?"Conhecimento compartilhado":(data.scope_labels?.[scope]||scope));
  scopeSelect.value=data.scopes.includes(old)?old:"";
  $("followup-tab-count").textContent = (data.open_questions || []).length;
  $("review-tab-count").textContent = data.proposal_count || 0;
  renderOpenQuestions(data.open_questions||[], data.scope_labels||{});
  const records=$("records"); records.replaceChildren();
  if(!data.records.length) records.append(el("div","empty","Nenhuma anotação encontrada. Tente outra busca ou projeto."));
  for(const item of data.records){
    const card=el("article","record"), main=el("div"), head=el("div","record-head");
    head.append(el("h3",null,item.title),el("span","badge"+(item.status==="active"?"":" removed"),typeLabels[item.kind]||item.kind),el("span","scope-tag",item.scope));
    main.append(head); card.append(main);
    const actions=el("div","record-actions"), button=el("button","text-button "+(item.status==="active"?"":"restore"),item.status==="active"?"Remover da busca":"Restaurar");
    button.type="button"; button.addEventListener("click",()=>changeStatus(item)); actions.append(button);
    if(item.status!=="active"){const erase=el("button","text-button","Apagar");erase.type="button";erase.addEventListener("click",()=>deleteRecord(item));actions.append(document.createTextNode(" · "),erase);}
    card.append(actions);
    card.append(el("p",null,item.content));
    const source=el("div","source"); source.append(el("b",null,"Fonte: "),document.createTextNode(item.source));
    if(item.evidence?.length){source.append(document.createTextNode(" · Evidência: "+item.evidence.join("; ")));} card.append(source);
    const date=item.created_at?new Date(item.created_at).toLocaleDateString("pt-BR"):""; card.append(el("span","date",date)); records.append(card);
  }
  $("record-limit").textContent=data.query?"Mostrando até 100 resultados. Refine a busca para encontrar outros.":"Mostrando até 200 anotações. Use a busca para encontrar outras.";
  $("record-limit").classList.toggle("hidden",data.records.length<(data.query?100:200));
  const preview=data.context_preview;
  const previewPanel=$("context-panel");
  previewPanel.classList.toggle("hidden",!preview);
  if(preview){
    $("context-estimate").textContent=`· ${preview.characters.toLocaleString("pt-BR")} caracteres · cerca de ${preview.estimated_tokens.toLocaleString("pt-BR")} tokens`;
    $("context-text").textContent=preview.content;
  }
  renderProposals(data.proposals||[], data.proposal_count||0);
  renderSources(data.source_hits||[], Boolean($("search").value.trim()));
  renderHandoff(data.handoff); renderPolicies(data.policies); renderOrganization(data.organization);
}
function renderOpenQuestions(items, labels){
  const select=$("question-scope"), selected=select.value, counts=new Map();
  for(const item of items) counts.set(item.scope,(counts.get(item.scope)||0)+1);
  select.replaceChildren();
  addOption(select,"",`Todos os projetos (${items.length})`);
  const scopes=[...counts.keys()].sort((a,b)=>(labels[a]||a).localeCompare(labels[b]||b,"pt-BR"));
  for(const scope of scopes){
    const label=scope==="global"?"Conhecimento compartilhado":scope==="default"?"Sem projeto":(labels[scope]||scope);
    addOption(select,scope,`${label} (${counts.get(scope)})`);
  }
  select.value=counts.has(selected)?selected:"";
  const visible=select.value?items.filter(item=>item.scope===select.value):items;
  const box=$("open-question-list");box.replaceChildren();
  $("open-questions-count").textContent=`(${visible.length})`;
  if(!visible.length){
    box.append(el("div","empty",items.length?"Nenhuma dúvida neste projeto.":"Nenhuma anotação marcada como dúvida está em aberto."));
    return;
  }
  for(const item of visible){
    const card=el("article","open-question"),head=el("div","open-question-head");
    const project=item.scope==="global"?"Conhecimento compartilhado":item.scope==="default"?"Sem projeto":(labels[item.scope]||item.scope);
    head.append(el("h3",null,item.title),el("span","question-project",project));
    card.append(head,el("p",null,item.content));
    const meta=el("small","open-question-source","Fonte: "+item.source);
    if(item.created_at)meta.append(document.createTextNode(" · "+new Date(item.created_at).toLocaleDateString("pt-BR")));
    card.append(meta);box.append(card);
  }
}
function renderSources(items, searched){const box=$("sources");box.replaceChildren();$("sources-panel").classList.toggle("hidden",!searched);if(!searched)return;if(!items.length){box.append(el("div","empty","Nenhum trecho de documento-fonte encontrado para esta busca."));return;}for(const item of items){const card=el("article","source-hit"),head=el("div","source-hit-head");head.append(el("strong",null,item.heading||item.source),el("span","scope-tag",item.scope));card.append(head,el("p",null,item.content+(item.truncated?"…":"")),el("small",null,"Localizador: "+item.source));box.append(card);}box.append(el("p","small-note","Estes trechos ajudam a localizar material. Abra a fonte e confira o contexto antes de usar como evidência."));}
function renderProposals(items,total){$("approve-all").disabled=$("reject-all").disabled=!total||state.reviewing;const panel=$("proposals-panel"),box=$("proposals");box.replaceChildren();if(!total)box.append(el("div","empty","Nenhuma sugestão aguardando revisão."));$("proposals-title").textContent=`Sugestões de memória (${total})`;$("proposal-limit").classList.toggle("hidden",total<=items.length);for(const proposal of items){const memory=proposal.record,card=el("article","proposal-card"),head=el("div","record-head");head.append(el("h3",null,memory.title),el("span","scope-tag",memory.scope));card.append(head,el("p","proposal-content",memory.content));card.append(el("small","source","Origem: "+memory.source));if(memory.evidence?.length)card.append(el("small","source","Evidência: "+memory.evidence.join(" · ")));if(proposal.possible_matches?.length){card.append(el("strong","proposal-matches-title","Possíveis anotações parecidas"));for(const match of proposal.possible_matches){const matchCard=el("div","proposal-match");matchCard.append(el("strong",null,match.title),el("p",null,match.content),el("small",null,`${match.scope} · ${match.source}`));card.append(matchCard);}}else card.append(el("p","small-note","Nenhuma anotação parecida apareceu na busca textual."));const actions=el("div","proposal-actions"),approve=el("button","refresh-button proposal-approve","Aprovar e guardar"),reject=el("button","text-button proposal-reject","Recusar");approve.type=reject.type="button";approve.addEventListener("click",()=>reviewProposal(proposal,"approve"));reject.addEventListener("click",()=>reviewProposal(proposal,"reject"));actions.append(approve,reject);card.append(actions);box.append(card);}}
async function reviewAllProposals(action) {
  if (state.reviewing) return;
  const ids = [...(state.data?.proposal_ids || [])];
  if (!ids.length) return;
  const project = $("scope").selectedOptions[0]?.textContent || "Todos os projetos";
  const verb = action === "approve" ? "aprovar e guardar" : "recusar";
  if (!confirm(`Deseja ${verb} todas as ${ids.length} sugestões de “${project}”?\nInclui as compartilhadas e as que não aparecem nas 20 exibidas. Sugestões novas que chegarem depois ficam para outra revisão.`)) return;
  state.reviewing = true;
  document.querySelectorAll("#proposals-panel button").forEach(button => button.disabled = true);
  $("proposal-bulk-status").textContent = "Revisando as sugestões…";
  try {
    const response = await fetch("/api/proposals/review-batch", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ids, action})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Não foi possível revisar as sugestões. Atualize a lista antes de tentar novamente.");
    $("proposal-bulk-status").textContent = data.message;
    toast(data.message);
  } catch (error) {
    $("proposal-bulk-status").textContent = error.message;
    toast(error.message);
  } finally {
    state.reviewing = false;
    await load();
  }
}
$("approve-all").addEventListener("click", () => reviewAllProposals("approve"));
$("reject-all").addEventListener("click", () => reviewAllProposals("reject"));
async function reviewProposal(proposal,action){if(state.reviewing)return;const verb=action==="approve"?"guardar na memória de busca":"recusar";if(!confirm(`Deseja ${verb} “${proposal.record.title}”?`))return;try{const response=await fetch(`/api/proposals/${encodeURIComponent(proposal.id)}/review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});const data=await response.json();if(!response.ok)throw new Error(data.error||"Não foi possível revisar a sugestão.");toast(data.message||"Sugestão revisada.");load();}catch(error){toast(error.message);}}
function renderHandoff(item){const box=$("handoff");box.replaceChildren();if(!item){box.className="empty";box.textContent="Nenhuma passagem para este projeto.";return;}box.className="handoff-card";box.append(el("strong",null,item.status||"Em andamento"),el("p",null,item.summary||""));for(const [label,key] of [["Próximas ações","next_actions"],["Pendências","blockers"],["Perguntas","open_questions"]])if(item[key]?.length){box.append(el("strong",null,label));box.append(el("p",null,item[key].join(" · ")));}if(item.source)box.append(el("small",null,"Origem: "+item.source));}
function renderPolicies(items){const box=$("policies");box.replaceChildren();if(!items.length){box.append(el("p","empty","Nenhuma regra cadastrada ainda."));return;}for(const item of items){const card=el("article","policy");card.append(el("strong",null,item.title||item.name||"Regra"),el("p",null,item.content||item.summary||item.rule||""));if(item.source)card.append(el("small",null,"Fonte: "+item.source));box.append(card);}}
function renderOrganization(org){const summary=$("organization-summary"),agents=$("agents"),skills=$("skills"),shared=$("shared-knowledge"),sources=$("project-sources");summary.replaceChildren();agents.replaceChildren();skills.replaceChildren();shared.replaceChildren();sources.replaceChildren();if(!org?.scope){summary.append(el("p","empty","Escolha um projeto no filtro acima para revisar sua organização."));return;}summary.append(el("p","organization-name",org.display_name),el("p","small-note",`${org.active_memory_count} anotações ativas · ${org.unknown_count} dúvidas em aberto · ${(org.sources||[]).length} documentos-fonte`));if(org.handoff)summary.append(el("p","organization-handoff","Última passagem: "+(org.handoff.summary||org.handoff.status||"em andamento")));if(!org.agents.length)agents.append(el("p","empty","Nenhum subagente específico cadastrado. A conversa central pode usar as skills do projeto."));for(const item of org.agents){const card=el("article","skill");card.append(el("strong",null,item.name),el("p",null,item.activation||"Apoio especializado"),el("small",null,"Papel: "+item.role_file+(item.workstream?" · Frente: "+item.workstream:"")));agents.append(card);}if(!org.skills.length)skills.append(el("p","empty","Nenhuma skill aplicável cadastrada."));for(const item of org.skills){const card=el("article","skill");card.append(el("strong",null,item.name),el("p",null,item.description),el("small",null,skillInheritanceLabel(item)));skills.append(card);}if(!org.shared_project_knowledge.length)shared.append(el("p","empty","Nenhum conhecimento compartilhado entre os especialistas deste projeto."));for(const item of org.shared_project_knowledge){const card=el("article","skill");card.append(el("strong",null,item.name),el("p",null,"Conhecimento compartilhado entre os especialistas do projeto."),el("small",null,"Usado por: "+(item.used_by||[]).join(" · ")));shared.append(card);}if(!org.sources.length)sources.append(el("p","empty","Nenhum documento-fonte importado para este projeto."));else{const details=el("details","project-sources-details"),label=el("summary",null,`Mostrar ${org.sources.length} documentos e históricos`),list=el("ul","project-source-list");for(const item of org.sources){const li=el("li"),name=el("span",null,item.title),path=el("small",null,item.path),remove=el("button","text-button","Remover fonte atual");remove.type="button";remove.addEventListener("click",()=>deleteSource(item));li.append(name,path,remove);list.append(li);}details.append(label,list,el("p","small-note","A remoção tira o arquivo das buscas atuais. Versões antigas ainda podem ficar no histórico do backup Git."));sources.append(details);}}
async function load(){const params=new URLSearchParams();if($("search").value.trim())params.set("q",$("search").value.trim());if($("scope").value)params.set("scope",$("scope").value);if($("archived").checked)params.set("archived","1");try{const response=await fetch("/api/dashboard?"+params);const data=await response.json();if(!response.ok)throw new Error(data.error||"Falha ao carregar.");render(data);}catch(error){toast(error.message);}}
async function changeStatus(item){const next=item.status==="active"?"retracted":"active";if(next==="retracted"&&!confirm("Tirar esta anotação das buscas? Ela continuará guardada no histórico do Git."))return;try{const response=await fetch(`/api/records/${encodeURIComponent(item.id)}/status`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:next})});const data=await response.json();if(!response.ok)throw new Error(data.error||"Não foi possível atualizar.");toast(next==="active"?"Anotação restaurada.":"Anotação arquivada.");load();}catch(error){toast(error.message);}}
async function deleteRecord(item){if(!confirm("Apagar esta anotação dos arquivos atuais? Esta ação não pode ser desfeita pelo painel. Cópias antigas ainda podem existir no histórico do Git."))return;try{const response=await fetch(`/api/records/${encodeURIComponent(item.id)}`,{method:"DELETE"});const data=await response.json();if(!response.ok)throw new Error(data.error||"Não foi possível apagar.");toast("Anotação apagada dos arquivos atuais.");load();}catch(error){toast(error.message);}}
async function deleteSource(item){if(!confirm(`Remover “${item.path}” dos arquivos atuais da OMM? A fonte deixa de aparecer nas buscas. Versões anteriores podem continuar no histórico do Git.`))return;try{const response=await fetch("/api/sources",{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:item.path,expected_sha256:item.sha256})});const data=await response.json();if(!response.ok)throw new Error(data.error||"Não foi possível remover a fonte.");toast("Fonte removida dos arquivos atuais.");load();}catch(error){toast(error.message);}}
async function syncBackup(){if(!confirm("Sincronizar com o backup Git agora? A OMM vai salvar as alterações atuais, buscar atualizações e enviar a versão combinada."))return;const button=$("sync");button.disabled=true;try{const response=await fetch("/api/sync",{method:"POST"});const data=await response.json();if(!response.ok)throw new Error(data.error||"Não foi possível sincronizar.");toast(data.message||"Backup sincronizado.");load();}catch(error){toast(error.message);}finally{button.disabled=false;}}
$("sync").addEventListener("click",syncBackup);$("refresh").addEventListener("click",load);$("scope").addEventListener("change",load);$("question-scope").addEventListener("change",()=>renderOpenQuestions(state.data?.open_questions||[],state.data?.scope_labels||{}));$("archived").addEventListener("change",load);$("search").addEventListener("input",()=>{clearTimeout(state.timer);state.timer=setTimeout(load,250);});load();

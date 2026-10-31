# Apuração Sem Biometria

Painel que soma, a partir dos **Boletins de Urna** publicados pelo TSE, quantos eleitores foram liberados para votar **sem a leitura da digital**, por turno, UF e município.

## Como funciona

- **Buscar no TSE**: o servidor consulta o Portal de Dados Abertos (`resultados-2026-boletim-de-urna`), baixa os ZIPs `bweb_*` dos estados escolhidos e processa um por vez. Nada passa pelo seu computador.
- **Enviar arquivo**: para quando você já tem o ZIP/CSV. O navegador envia em partes de 8 MB (com nova tentativa automática), então arquivos de vários GB funcionam.
- Cada seção aparece no CSV em várias linhas (uma por candidato/cargo). O app conta **um valor por seção** e soma por município, UF e turno.
- A coluna de liberação sem digital é detectada pelo nome (começa com `QT_` e contém `BIOM`, `NASC`, `LIB` ou `HABIL`), porque o leiaute muda entre eleições. Se nenhuma for encontrada, a tarefa mostra a lista de colunas do arquivo.
- Processar de novo um mesmo turno + UF substitui o resultado **atual**, mas cada busca ou envio concluído fica guardado como **rodada arquivada**. No painel, "Ver" abre a rodada (totais, tabelas, municípios, CSV e PDF daquela rodada) e "Excluir" a remove. Na primeira execução desta versão, os resultados que já existiam viram a rodada "Resultados anteriores ao arquivamento".
- O relatório separa **1º e 2º turno**: um total e uma tabela por UF para cada turno; na impressão, cada turno começa em página nova.
- Exporta CSV por UF ou por município (separador `;`, abre direto no Excel).

## Votos após as 17h (logs de urna)

O boletim de urna não tem horário. Para saber quantos votos foram registrados depois das 17h, o app lê os **logs das urnas** (`.logjez`), que anotam a hora de cada evento.

- **Busca automática:** em "Buscar logs no TSE", escolha turno e estados. O servidor usa o conjunto **"Resultados 2026 - Arquivos transmitidos para totalização"** do Portal de Dados Abertos, onde o TSE publica, por UF e turno, um ZIP com os logs das urnas junto com BU, RDV e assinaturas (`bu_imgbu_logjez_rdv_vscmr_2026_2t_UF.zip`). O app baixa o ZIP de cada UF, lê só os `.logjez` e apaga o arquivo. Os logs do sistema de preparação (GEDAI) são ignorados. Como esses ZIPs incluem também o RDV, são bem maiores que o boletim de urna. "Ver arquivos disponíveis" mostra o que foi encontrado antes de começar. Se o TSE publicar com um nome que a busca não reconheça, defina `TSE_LOG_DATASET` com o nome do conjunto (pode ser mais de um, separados por vírgula). `TSE_ANO` muda o ano (padrão 2026).
- **Envio manual:** use "Enviar arquivo" com o ZIP de logs do Portal de Dados Abertos (ou um `.logjez` avulso), ou cole o link do arquivo em "Baixar logs pelo endereço" (só aceita endereços `tse.jus.br`). ZIPs dentro de ZIPs são abertos automaticamente.
- **O que é contado:** cada registro de "voto computado" é um voto. Ele conta como "sem digital" se, desde o início daquele eleitor, apareceu uma mensagem de liberação pelo procedimento alternativo. O voto conta como "após as 17h" pela hora do registro de voto computado.
- **Fuso:** a votação vai das 8h às 17h de Brasília, e a urna grava a hora local. No modo padrão o corte é ajustado por UF (AC 15h; AM, RO, RR, MT, MS 16h no relógio local). Também há o modo "17h no relógio da urna, sem ajuste". O exterior (ZZ) vota em hora local. O oeste do Amazonas e Fernando de Noronha têm fuso diferente da capital e não são ajustados.
- **Conferência:** a tabela mostra a mediana do 1º e do último voto por UF (para checar o relógio) e o total "sem biometria" do boletim de urna ao lado do total "sem digital" dos logs. Os dois devem ficar próximos quando a UF inteira foi processada.
- **Padrões de mensagem:** o texto das mensagens muda entre versões da urna. Os três padrões (início de eleitor, liberação sem digital, voto computado) são editáveis no painel, e cada processamento lista as mensagens mais frequentes do log, marcadas pelos padrões atuais. Na primeira vez, envie um `.logjez` de uma seção, confira as marcações e ajuste antes de processar uma UF inteira.
- **Identificação:** zona e seção vêm do nome do arquivo (`…MMMMMZZZZSSSS.logjez`); UF e turno vêm do caminho (ex.: `logs_2t_SP.zip`) ou, se não houver, dos campos do formulário.
- Votar depois das 17h é permitido para quem estava na fila; o número não indica irregularidade por si só.
- Variáveis: `LOG_WORKERS` (processos em paralelo, padrão até 4) e `LOG_URL_HOSTS` (domínios aceitos no download por endereço, padrão `tse.jus.br`). Os logs de uma UF grande ocupam vários GB: use o volume em `/data`.

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
# abra http://localhost:8000
```

## Deploy no Railway

1. Suba esta pasta para um repositório no GitHub.
2. No Railway: **New Project → Deploy from GitHub repo** e escolha o repositório. O `railway.json` já define o comando de start e o health check.
3. **Volume (recomendado)**: em *Settings → Volumes*, crie um volume montado em `/data` (5–10 GB). Ele guarda o banco e os arquivos temporários, que no estado de SP passam de 1 GB.
4. **Variáveis** (*Variables*):

| Variável | Valor sugerido | Para quê |
|---|---|---|
| `DATA_DIR` | `/data` | Onde fica o SQLite (persiste entre deploys com o volume) |
| `TMP_DIR` | `/data/tmp` | Arquivos baixados/enviados durante o processamento |
| `APP_PASSWORD` | uma senha | Protege o painel com login (usuário: qualquer) |
| `MAX_UPLOAD_GB` | `6` | Limite de tamanho por envio |
| `TSE_DATASET` | `resultados-2026-boletim-de-urna` | Troque para outro ano, ex. `resultados-2022-boletim-de-urna` |

5. Em *Settings → Networking*, clique em **Generate Domain** e abra o endereço.

> Mantenha **1 worker** no gunicorn (já configurado): a fila de tarefas e o controle dos envios ficam na memória do processo.

## Estrutura

```
app.py          rotas Flask, fila de tarefas, envio em partes
processor.py    leitura em streaming dos ZIP/CSV e agregação por seção/município
tse.py          consulta ao Portal de Dados Abertos e download
logs.py         leitura dos logs de urna (.logjez) e contagem após as 17h
db.py           SQLite (resultados e tarefas)
templates/      painel
static/         CSS e JS
tests/          teste com boletim simulado
```

## Teste

```bash
python -m unittest discover tests
```

## Observações sobre os dados

- O número soma, em geral, eleitores **com biometria cadastrada** que foram habilitados sem a digital (falha de leitura → conferência do ano de nascimento). Eleitores **sem** cadastro biométrico podem não estar nessa coluna; confira o leia-me do conjunto no portal do TSE.
- Os dados do TSE não permitem saber em quem esses eleitores votaram.

# Apuração Sem Biometria

Painel que soma, a partir dos **Boletins de Urna** publicados pelo TSE, quantos eleitores foram liberados para votar **sem a leitura da digital**, por turno, UF e município.

## Como funciona

- **Buscar no TSE**: o servidor consulta o Portal de Dados Abertos (`resultados-2026-boletim-de-urna`), baixa os ZIPs `bweb_*` dos estados escolhidos e processa um por vez. Nada passa pelo seu computador.
- **Enviar arquivo**: para quando você já tem o ZIP/CSV. O navegador envia em partes de 8 MB (com nova tentativa automática), então arquivos de vários GB funcionam.
- Cada seção aparece no CSV em várias linhas (uma por candidato/cargo). O app conta **um valor por seção** e soma por município, UF e turno.
- A coluna de liberação sem digital é detectada pelo nome (começa com `QT_` e contém `BIOM`, `NASC`, `LIB` ou `HABIL`), porque o leiaute muda entre eleições. Se nenhuma for encontrada, a tarefa mostra a lista de colunas do arquivo.
- Processar de novo um mesmo turno + UF substitui o resultado anterior.
- Exporta CSV por UF ou por município (separador `;`, abre direto no Excel).

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

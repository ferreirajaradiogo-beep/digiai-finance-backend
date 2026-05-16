# Deploy do backend no Render

Este backend ja esta preparado para subir no Render com:

- FastAPI
- PostgreSQL gratuito do Render
- CORS liberado para a beta web publicada e para testes locais

## Pasta

`C:\Users\ferre\OneDrive\Desktop\PROJETOS\CONTROLE DE DESPESA\NOTE NOVO\NOTAFACIL_BACKEND`

## Arquivos prontos

- `render.yaml`
- `requirements.txt`
- `.env.example`

## Como publicar

### 1. Suba a pasta para um repositório Git

Use a pasta do backend como um repositório próprio no GitHub.

### 2. No Render, crie um Blueprint

Selecione o repositório do backend e deixe o Render ler o `render.yaml`.

### 3. O blueprint cria

- web service `notafacil-api`
- banco `notafacil-db`

### 4. Variáveis importantes

Ja definidas:

- `APP_NAME=DigiAI Finance API`
- `APP_ENV=production`
- `DEBUG_RESET_CODE=false`
- `SECRET_KEY` gerada automaticamente
- `DATABASE_URL` vinda do banco do Render
- `CORS_ORIGINS=https://digiai-finance-beta.onrender.com,http://127.0.0.1:5000,http://localhost:5000`

## Teste apos deploy

Abra:

- `/health`
- `/docs`

Depois confirme:

1. cadastro via web
2. login via web
3. cadastro/login via app
4. criacao de conta/categoria/lancamento
5. troca de plano

## Depois de publicar

Pegue a URL do backend, por exemplo:

`https://notafacil-api.onrender.com`

e configure no web service da beta:

- `NOTAFACIL_BACKEND_URL=https://notafacil-api.onrender.com`

Assim a web e o app passam a usar a mesma base online.

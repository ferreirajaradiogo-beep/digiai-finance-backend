# Dig.ai Finanças Backend

Backend SaaS oficial do Dig.ai Finanças, construído com FastAPI.

## Objetivo

Este backend existe para centralizar:

- autenticação
- multiusuário real
- sincronização entre app, site e programa
- backup na nuvem
- recuperação de senha
- evolução futura para SaaS completo

## Stack

- FastAPI
- SQLAlchemy
- JWT
- SQLite local para desenvolvimento
- PostgreSQL preparado via `DATABASE_URL`
- SMTP preparado para recuperação de senha

## Estrutura principal

- `app/main.py`: rotas e endpoints principais
- `app/models.py`: modelos do banco
- `app/schemas.py`: contratos de entrada e saída
- `app/security.py`: senha, token e autenticação
- `app/database.py`: engine, sessão e base
- `app/config.py`: variáveis e configurações
- `app/email_service.py`: envio de código de recuperação
- `render.yaml`: base para deploy
- `smoke_test.py`: teste rápido da API

## O que já existe

- cadastro
- login
- senha criptografada
- JWT
- recuperação de senha por código
- envio SMTP preparado
- multiusuário real
- contas
- categorias
- transações
- backup em JSON
- sync inicial com `/sync/push` e `/sync/pull`
- configuração do usuário
- preparação para PostgreSQL
- fallback em SQLite local

## Modelos principais

- `User`
- `Account`
- `Category`
- `Transaction`
- `Backup`

## Endpoints atuais

### Saúde e raiz

- `GET /health`
- `GET /`

### Autenticação

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/forgot-password`
- `POST /auth/reset-password`

### Usuário

- `GET /me`
- `PATCH /me/settings`

### Contas

- `GET /accounts`
- `POST /accounts`
- `PUT /accounts/{account_id}`
- `DELETE /accounts/{account_id}`

### Categorias

- `GET /categories`
- `POST /categories`
- `PUT /categories/{category_id}`
- `DELETE /categories/{category_id}`

### Transações

- `GET /transactions`
- `POST /transactions`
- `PUT /transactions/{transaction_id}`
- `DELETE /transactions/{transaction_id}`

### Backup e sync

- `POST /backup`
- `POST /sync/push`
- `GET /sync/pull`

## Regras de plano já implementadas

Hoje o backend já aplica limites básicos do plano grátis:

- até 20 lançamentos por mês
- até 1 conta
- até 10 categorias

## Rodar localmente

```powershell
cd "C:\Users\ferre\OneDrive\Desktop\PROJETOS\CONTROLE DE DESPESA\NOTE NOVO\NOTAFACIL_BACKEND"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Depois abra:

- `http://127.0.0.1:8000/docs`
- ou no celular, na mesma rede:
- `http://SEU_IP_LOCAL:8000/docs`

## Teste rápido

```powershell
.\.venv\Scripts\python smoke_test.py
```

Se aparecer:

```text
Dig.ai Finanças backend OK
```

significa que a API conseguiu:

- criar usuário
- fazer login
- listar dados
- criar transação
- salvar backup
- testar sincronização básica

## Variáveis importantes

- `SECRET_KEY`
- `DATABASE_URL`
- `CORS_ORIGINS`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`

## PostgreSQL

Exemplo:

```text
postgresql://usuario:senha@host:5432/notafacil
```

Quando essa variável estiver configurada corretamente, o backend pode sair do SQLite local e usar banco de produção.

## Relação com o app e o site

Este backend é a ponte oficial entre:

- app mobile React Native
- site/PWA Flask
- futura versão desktop/EXE

O objetivo é usar a mesma conta do usuário em todas as frentes.

## Estado atual para mercado

Já pronto:

- autenticação
- multiusuário
- senha criptografada
- contas, categorias e transações
- backup
- recuperação de senha preparada
- base para deploy

Ainda pendente:

- PostgreSQL em produção
- e-mail real validado em produção
- sincronização total madura entre todas as frentes
- billing do plano Pro
- monitoramento/logs de produção

## Próximos passos recomendados

1. subir backend online
2. conectar site e app ao backend real de forma estável
3. finalizar recuperação de senha com e-mail real
4. adicionar cobrança para plano Pro
5. criar painel administrativo
6. preparar ambiente de produção com PostgreSQL e CORS final

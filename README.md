# Portal

![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-20232A?logo=react&logoColor=61DAFB)
![Podman](https://img.shields.io/badge/Podman-892CA0?logo=podman&logoColor=white)
![Railpack](https://img.shields.io/badge/Build-Railpack-blue)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
[![AI-Assisted Development](https://img.shields.io/badge/Development-AI--Assisted-8A2BE2?logo=openai&logoColor=white)](https://github.com/features/copilot)

**Cole a URL de um repositório Git e obtenha uma aplicação em execução com uma URL compartilhável.**

O Portal é uma plataforma de implantação auto-hospedada, projetada para operar em um único servidor. Ele compila aplicações a partir de repositórios Git usando o Railpack e as executa em contêineres Podman sem privilégios de root, sem exigir que o usuário configure Dockerfiles ou gerencie contêineres manualmente.

Criado com fluxos de trabalho assistidos por IA, contando com direção humana, decisões arquiteturais, integração e validação.

### Funcionalidades

- **Implantação de Git para contêiner:** Detecta automaticamente o processo de compilação e gera uma imagem OCI.
- **URLs compartilháveis e logs:** Acesse as aplicações implantadas e consulte os logs de compilação e execução.
- **Gerenciamento do ciclo de vida:** Inicie, interrompa e exclua implantações, com parada automática por inatividade.
- **Aplicação de políticas:** Lista de permissões de repositórios, cotas de implantação e controles para interromper toda a plataforma.
- **Gerenciamento de segredos:** Entrada mascarada e criptografia por envelope para acesso pelo executor.
- **Trilha de auditoria:** Registro de eventos somente para acréscimo, aplicado às operações de escrita.

Atualmente, o Portal utiliza uma única identidade interna (`dev@portal.local`). Autenticação, acesso multiusuário e permissões baseadas em funções ainda não foram implementados.

## Arquitetura

O Portal separa a API do processo responsável por compilar e executar as aplicações. Ambos utilizam um banco de dados SQLite compartilhado.

| Componente | Responsabilidade |
| --- | --- |
| `api/` | Aplicação FastAPI e API de implantação |
| `runner/` | Execução de compilações, ciclo de vida dos contêineres e limpeza por inatividade |
| `core/` | Lógica de domínio e aplicação de políticas |
| `adapters/` | Persistência, criptografia e armazenamento de logs |
| `frontend/` | Aplicação web SPA com React, Vite e Tailwind |
| `ops/` | Provisionamento, serviços systemd, Caddy, firewall e manual operacional |
| `tests/`, `e2e/` | Testes unitários, de integração e de interface |

O ambiente de desenvolvimento pode utilizar a mesma cadeia real de compilação e execução: Railpack, BuildKit e Podman sem privilégios de root.

## Início rápido

### Pré-requisitos

- Python e Node.js/npm.
- Railpack, BuildKit e Podman sem privilégios de root para implantações reais.
- Daemon do BuildKit em funcionamento e Podman configurado.

Consulte [`ops/RUNBOOK.md`](ops/RUNBOOK.md) para conhecer os requisitos do ambiente de execução.

### Execução local

Crie o ambiente virtual Python e instale as dependências:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Inicie a API:

```bash
.venv/bin/python -m api.server
```

Em um segundo terminal, inicie o executor com o ambiente real de contêineres:

```bash
make runner-podman
```

Em um terceiro terminal, inicie o frontend:

```bash
cd frontend
npm install
npm run dev
```

Acesse [http://localhost:5173](http://localhost:5173).



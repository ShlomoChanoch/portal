# 🐳 Portal - Docker Container Launcher

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/downloads/) [![FastAPI](https://img.shields.io/badge/FastAPI-0.68%2B-green)](https://fastapi.tiangolo.com/) [![Docker](https://img.shields.io/badge/Docker-20.10%2B-blue)](https://www.docker.com/products/docker-desktop/) [![JavaScript](https://img.shields.io/badge/JavaScript-ES6%2B-yellow)](https://developer.mozilla.org/en-US/docs/Web/JavaScript) [![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=flat&logo=html5&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/HTML)

Uma aplicação simples e leve criada para criar contêineres Docker de forma dinâmica através de uma interface gráfica. O projeto conta com um frontend minimalista em HTML5/JavaScript e um backend em Python com FastAPI integrado ao CLI do Docker.

---

## 📌 Sumário

- [Visão Geral](#-visão-geral)
- [Tecnologias Utilizadas](#-tecnologias-utilizadas)
- [Estrutura do Projeto](#-estrutura-do-projeto)
- [Pré-requisitos](#-pré-requisitos)
- [Como Executar](#-como-executar)
  - [1. Clonar o Repositório](#1-clonar-o-repositório)
  - [2. Configurar e Executar o Backend](#2-configurar-e-executar-o-backend)
  - [3. Executar o Frontend](#3-executar-o-frontend)
- [Como Usar](#-como-usar)
- [API Endpoints](#-api-endpoints)

---

## 🌐 Visão Geral

O **Portal** permite que desenvolvedores subam contêineres Docker preenchendo um formulário simples com as seguintes informações:
- **Nome do Contêiner** (`container_name`): Identificador único do contêiner.
- **Imagem Docker** (`container_image`): Nome e tag da imagem (ex: `nginx:alpine`, `redis:latest`).
- **Porta Mapeada** (`port`): Mapeamento de porta no formato `host:container`.

A requisição é processada pela API FastAPI, que executa o comando `docker run` no ambiente host e retorna o ID do contêiner gerado ou uma mensagem de erro detalhada.

---

## 🛠️ Tecnologias Utilizadas

### **Frontend**
- **HTML5**: Interface limpa baseada em formulários.
- **JavaScript (ES6+)**: Manipulação do DOM e comunicação assíncrona com a API usando `fetch`.

### **Backend**
- **Python 3**: Linguagem de programação backend.
- **FastAPI**: Framework web moderno e assíncrono para construção de APIs RESTful.
- **Uvicorn**: Servidor web ASGI de alto desempenho.
- **Pydantic**: Validação de esquema dos dados do payload JSON.
- **Subprocess**: Módulo nativo para execução automatizada de comandos do Docker CLI no sistema operacional host.

---

## 📁 Estrutura do Projeto

```text
.
├── index.html     # Interface do usuário (Formulário web)
├── script.js      # Lógica JS (Captura dos campos e requisição HTTP)
├── main.py        # API FastAPI com endpoint para execução de contêineres
└── README.md      # Documentação do repositório
```

---

## 📋 Pré-requisitos

Antes de iniciar a aplicação, certifique-se de possuir em seu ambiente:

1. **Python 3.8+** instalado.
2. **Docker Engine / Docker Desktop** rodando e com permissão de execução sem `sudo`.
3. Gerenciador de pacotes **pip**.

---

## 🚀 Como Executar

### 1. Clonar o Repositório

```bash
git clone https://github.com/ShlomoChanoch/portal.git
cd portal
```

### 2. Configurar e Executar o Backend

1. Crie e ative um ambiente virtual:
   ```bash
   python -m venv .venv
   
   # Linux / macOS
   source .venv/bin/activate

   # Windows (PowerShell)
   .\venv\Scripts\Activate.ps1
   ```

2. Instale as dependências:
   ```bash
   pip install fastapi uvicorn pydantic
   ```

3. Inicie o servidor FastAPI na porta **3000**:
   ```bash
   uvicorn main:app --reload --port 3000
   ```
   > 💡 O backend ficará acessível em: `http://127.0.0.1:3000`

### 3. Executar o Frontend

Abra o arquivo `index.html` diretamente em seu navegador web ou utilize um servidor estático simples com Python:

```bash
# Executar na raiz do projeto em um novo terminal
python -m http.server 8000
```
Acesse no navegador: `http://localhost:8000` ou abra diretamente `index.html`.

---

## 🎯 Como Usar

1. Acesse a página do Portal no seu navegador.
2. Preencha os campos solicitados:
   - **Name**: Nome para o contêiner (ex: `meu-servidor`)
   - **Image**: Imagem Docker (ex: `nginx:alpine`)
   - **Port**: Porta para mapeamento (ex: `8080`)
3. Clique no botão **Submit**.
4. Em caso de sucesso, uma mensagem de confirmação exibirá o ID do contêiner criado.
5. Verifique a execução no terminal executando:
   ```bash
   docker ps
   ```

---

## 🔌 API Endpoints

### `POST /api/containers`

Recebe a requisição para iniciar um contêiner no host.

#### **Request Body** (`application/json`)
```json
{
  "container_name": "meu-servidor",
  "container_image": "nginx:alpine",
  "port": "8080"
}
```

#### **Response (Sucesso - 200 OK)**
```json
{
  "status": "success",
  "container_id": "c7a8b9d0e1f2..."
}
```

#### **Response (Erro - 200 OK)**
```json
{
  "status": "error",
  "message": "docker: Error response from daemon: Conflict. The container name "/meu-servidor" is already in use..."
}
```

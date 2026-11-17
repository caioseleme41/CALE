# SIAP — Sistema Inteligente de Agenda Pessoal

O **SIAP** reúne agenda e organização pessoal em uma interface simples. Você pode registrar compromissos manualmente ou descrevê-los em linguagem natural para receber uma sugestão de título, data, horário e categoria antes de salvar.

> **Status:** em desenvolvimento. Esta versão já oferece agenda, contas de usuário e persistência em MySQL. Alguns recursos da proposta original ainda são planos futuros.

## Funcionalidades disponíveis

### Agenda e compromissos

- Painel com os compromissos do dia e indicadores de tarefas pendentes e concluídas.
- Calendário mensal e visualização dos compromissos por data.
- Cadastro manual com título, data, horário e categoria.
- Marcação de tarefas como concluídas, filtros e exclusão manual.
- Exclusão pelo assistente: descreva qual compromisso deseja remover; o SIAP apresenta a correspondência e pede confirmação antes de apagar.
- Adição rápida por texto, com identificação de expressões comuns de data, dia da semana e horário. O assistente sugere título e categoria e permite revisar os dados antes de salvar.

A interpretação de texto desta versão usa regras locais em português; não depende de um serviço externo de IA. As categorias disponíveis incluem Estudos, Saúde, Financeiro, Casa, Compras, Carro, Compromissos, Trabalho e Pessoal.

### Contas e perfil

- Cadastro e login com e-mail e senha, encerramento de sessão e confirmação do e-mail por código.
- Código de confirmação com 8 dígitos, válido por 15 minutos. O envio depende de um servidor SMTP configurado.
- Página **Minha conta** para editar nome, foto de perfil e telefone, além de guardar a preferência de alertas por SMS.
- Alteração de senha com indicador de força, verificação de senha atual e bloqueio de senhas comuns ou repetitivas.
- Senhas protegidas no servidor com derivação criptográfica e salt individual.

A preferência de SMS é armazenada, mas o envio de mensagens ainda precisa de integração com um provedor. O login com Google não está incluído nesta versão.

### Persistência dos dados

- Os compromissos de uma conta autenticada ficam no MySQL e são associados ao usuário conectado.
- No modo visitante, os compromissos ficam armazenados localmente no navegador.
- A aplicação também pode importar compromissos locais antigos para a conta quando o usuário entra.

## Tecnologias

- **Interface:** HTML5, CSS3 e JavaScript.
- **Servidor/API:** Python 3.13, usando a biblioteca padrão para servir a aplicação e responder às requisições.
- **Banco de dados:** MySQL 8.4, acessado por `mysql-connector-python`.
- **E-mail de confirmação:** SMTP configurável por variáveis de ambiente.

## Estrutura do projeto

```text
SIAP/
├── index.html                         # Estrutura da interface
├── style.css                          # Estilos e responsividade
├── script.js                          # Interações da agenda e comunicação com a API
├── logo.svg                           # Logo do SIAP
├── server.py                          # Servidor local e endpoints da API
├── schema.sql                         # Criação do banco e das tabelas atuais
├── migration_account_settings.sql    # Migração para bancos anteriores
├── migration_email_verification.sql  # Migração para confirmação de e-mail
├── requirements.txt                   # Dependências Python
├── setup_mysql.ps1                    # Preparação do MySQL local no Windows
├── start_siap.ps1                     # Inicialização do MySQL e da aplicação
├── iniciar_siap.bat                   # Atalho para iniciar o SIAP no Windows
├── .env.example                       # Modelo de configuração local
└── README.md                          # Documentação do projeto
```

## Como executar no Windows

### Pré-requisitos

- Windows PowerShell.
- Python 3.13.
- MySQL Server 8.4.

### Instalação

1. Abra o PowerShell na pasta do projeto.
2. Crie o ambiente Python e instale a dependência do MySQL:

   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Prepare o banco local executando o script uma vez:

   ```powershell
   .\setup_mysql.ps1
   ```

   O script cria o banco e as tabelas e grava as credenciais locais em `.env` e em `%LOCALAPPDATA%\SIAP\mysql-admin.cnf`.

4. Para permitir cadastro e confirmação de e-mail, preencha no `.env` as variáveis `SIAP_SMTP_*` com os dados do seu provedor SMTP. O arquivo `.env.example` mostra os nomes das variáveis. Sem SMTP funcional, o SIAP não consegue enviar o código de confirmação.
5. Inicie o aplicativo clicando duas vezes em `iniciar_siap.bat` e acesse [http://localhost:8000](http://localhost:8000). Mantenha a janela aberta enquanto estiver usando o SIAP.

Em um banco criado por uma versão anterior do SIAP, aplique `migration_account_settings.sql` e `migration_email_verification.sql` com uma conta administradora do MySQL. Em uma instalação nova, as tabelas atuais são criadas a partir de `schema.sql` pelo script de preparação.

## Configuração e segurança

- **Não publique o `.env`.** Ele pode conter senhas do banco e credenciais SMTP. O `.gitignore` exclui esse arquivo; publique apenas o `.env.example`, sem segredos.
- O perfil `SIAP-Workbench-connection.json`, o ambiente `.venv` e arquivos de cache são locais e não fazem parte do código-fonte necessário para publicar o projeto.
- A configuração padrão atende apenas ao computador local. Para permitir acesso de outros dispositivos, hospede o servidor e o banco em uma infraestrutura protegida, configure HTTPS e use credenciais próprias.
- A senha do Gmail ou de outro serviço de e-mail não deve ser colocada no código nem compartilhada no repositório.

## Próximas evoluções

A proposta do SIAP inclui centralizar outras áreas da rotina — como saúde, finanças, casa, estudos e veículo. Nesta versão, esses temas aparecem como categorias da agenda; módulos próprios, lembretes automáticos, envio de SMS e hospedagem para vários dispositivos podem ser desenvolvidos em etapas futuras.

## Licença

A licença do projeto ainda será definida. Até que isso aconteça, não presuma que o código esteja liberado para redistribuição ou uso sob uma licença de código aberto.


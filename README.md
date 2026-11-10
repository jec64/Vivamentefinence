# VivaMente V2

## Teste local
Extraia o pacote portátil para uma pasta nova, execute Iniciar.cmd e mantenha a janela aberta. A versão local usa somente o Excel de demonstração. Ela é exclusiva para testes no computador. Os painéis, cadastros, lançamentos, relatórios e edição/exclusão podem ser testados. Login, auditoria e gestão de senha dependem da versão hospedada.

## Hospedagem
O Dockerfile inicia Gunicorn com o aplicativo WSGI autenticado, separado do aplicativo de demonstração. Use um PostgreSQL privado e configure DATABASE_URL e PUBLIC_ORIGIN no painel de secrets do servidor. PUBLIC_ORIGIN deve ser a URL HTTPS exata, sem barra final. Termine TLS no proxy, encaminhe o Host original, aplique limites de tráfego também no proxy e mantenha o PostgreSQL inacessível pela internet. O aplicativo não confia em X-Forwarded-For enviado pelo navegador. Configure o proxy/WSGI para disponibilizar REMOTE_ADDR confiável se desejar distinguir origens; o limite global continua ativo. Cada worker abre um pool de 1 a 5 conexões. Dimensione os workers conforme o limite do banco.

Inicialize uma vez, com acesso administrativo ao banco:
- python postgres_store.py init
- Informe o e-mail, o nome e uma senha nova no prompt oculto. A senha deve ter de 12 a 128 caracteres. O comando cria a conta única.
- Para migrar, copie a base em uso no cliente e rode python postgres_store.py importar --base /caminho/Clinica.xlsx. O destino precisa estar vazio. A importação preserva IDs e compara registros e totais na mesma transação.
- Antes da virada, teste com uma cópia e valide login, publicação HTTPS e recuperação de backup no ambiente escolhido.

Dados de negócio são armazenados como registros JSONB identificados individualmente, com gravação apenas das linhas alteradas. A API lê os conjuntos necessários ao cálculo; os relatórios e lançamentos ainda processam o conjunto de registros em memória. A interface não faz polling. Para volumes maiores, será preciso medir e levar agregações e paginação de consultas ao PostgreSQL. Configure backups com retenção e teste a restauração.

## Segurança implementada
Conta única sem cadastro público, senha com scrypt, sessões revogáveis de 8 horas armazenadas por hash, cookie HttpOnly/Secure/SameSite, validação de Host e Origin, consultas parametrizadas, limite de tentativas por origem e global, CSP e proteção contra iframe. O relatório compartilhável usa uma projeção sem faturamento bruto ou parcela da clínica. Alterações gravadas e seus eventos de auditoria são confirmados na mesma transação. A troca de senha exige a atual e invalida as sessões existentes.

A auditoria mostra os 100 eventos mais recentes de cada categoria. Revisão do proxy, teste integrado com PostgreSQL, backups e validação da hospedagem fazem parte da liberação. A prévia do GitHub Pages, quando configurada, publica apenas a pasta docs com dados fictícios.

## Testes no GitHub
O workflow Testes da API e PostgreSQL cria um banco descartável em cada execução e verifica login, bloqueio de tentativas, proteção de origem, auditoria, vínculos de exclusão e relatórios. Aguarde a primeira execução antes de promover uma versão a produção. GitHub Pages publica somente a demonstração estática. A aplicação autenticada roda em uma hospedagem que execute o Dockerfile.

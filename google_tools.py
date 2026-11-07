#ETAPA 1: IMPORTAÇÃO DAS BIBLIOTECAS
import os #acessa os arquivos do sistema
import unicodedata #normaliza os caracteres especiais
import uuid #gera identificadores únicos(para criar os links do meet)
from datetime import datetime #trabalha com datas e horários
from google.auth.exceptions import RefreshError #trata erros de autenticação
from google.auth.transport.requests import Request #faz requisições HTTP
from google.oauth2.credentials import Credentials #gerencia as credenciais de autenticação
from google_auth_oauthlib.flow import InstalledAppFlow #gerencia o fluxo de autenticação
from googleapiclient.discovery import build #constrói o serviço da API (cria objeto de autentitação)


#ETAPA 2: CRIAR OS ESCOPOS DE AUTENTICAÇÃO
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',  #define o escopo de acesso ao Google Calendar
    'https://www.googleapis.com/auth/spreadsheets' #planilhas do google
] 
#Dicionario com os nomes das colunas nas planilhas e seus respectivos IDs
CAMPOS_DA_PLANILHA = {
    "nome" : {"nome", "nomecandidato"},
    "vaga" : {"vaga", "cargo"},
    "data" : {"data", "dataentrevista"},
    "hora" : {"hora", "horario", "horarioentrevista"},
    "data_hora_inicio": {"datahorainicio", "datainicioentrevista"},
    "data_hora_fim": {"datahorafim", "datafimentrevista"},
    "email" : {"email", "emailcandidato"},
    "status" : {"status", "situacao"},
}


#ETAPA 3: FUNÇÃO PARA ATENTICAÇÃO DO GOOGLE
def autenticar_google():
    """Autentica o usuário no Google e salva as credenciais locais."""
    credenciais = None

    if os.path.exists('token.json'):
        try:
            credenciais = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception:
            credenciais = None
            if os.path.exists('token.json'):
                os.remove('token.json')

    if credenciais and credenciais.expired and credenciais.refresh_token:
        try:
            credenciais.refresh(Request())
        except RefreshError:
            credenciais = None
            if os.path.exists('token.json'):
                os.remove('token.json')

    if not credenciais or not credenciais.valid:
        try:
            fluxo = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            credenciais = fluxo.run_local_server(port=0)
        except Exception as erro:
            raise RuntimeError(
                "Não foi possível autenticar no Google. Verifique: "
                "1) as APIs do Calendar e Sheets estão habilitadas; "
                "2) o arquivo credentials.json é de um projeto OAuth Desktop App; "
                "3) o consentimento OAuth está configurado e seu e-mail está em Test users."
            ) from erro

    try:
        with open('token.json', 'w', encoding='utf-8') as token:
            token.write(credenciais.to_json())
    except Exception:
        pass

    return credenciais


def agendar_entrevista_com_meet (
        nome_candidato,
        email_candidato,
        data_hora_inicio,
        data_hora_fim,
):
    """Função para agendar uma entrevista no google calendar com link do Google Meet"""
    servico = build('calendar', 'v3', credentials=autenticar_google()) #constrói o serviço da API do Google Calendar


    evento = {
        'summary': f'Entrevista com {nome_candidato}', #título do evento
        'description': f'Entrevista com o time de People.', #descrição do evento
        'start': {
            'dateTime': data_hora_inicio, #data e hora de início do evento
            'timeZone': 'America/Sao_Paulo', #fuso horário
        },
        'end': {
            'dateTime': data_hora_fim, #data e hora de término do evento
            'timeZone': 'America/Sao_Paulo', #fuso horário
        },
        'attendees': [
            {'email': email_candidato}, #adiciona o e-mail do candidato como participante
        ],
        'conferenceData': {
            'createRequest': {
                'requestId': str(uuid.uuid4()), #gera um ID único para a solicitação de criação do link do Meet
                'conferenceSolutionKey': {'type': 'hangoutsMeet'}, #define o tipo de conferência como Google Meet
            },
        },
    }


    evento_criado = servico.events().insert(
        calendarId='primary', #define o calendário principal do usuário
        body=evento, #passa o corpo do evento criado
        conferenceDataVersion=1, #define a versão da API de conferência
        sendUpdates='all', #envia atualizações para todos os participantes
    ).execute() #executa a criação do evento


    link_meet = evento_criado.get('hangoutLink') #obtém o link do Google Meet do evento criado
    return link_meet #retorna o link do Google Meet


def registrar_candidato_planilha(
        id_planilha,
        nome,
        email,
        vaga,
        status,
        data_hora_inicio=None,
        data_hora_fim=None
):
    """Função para registrar os dados do candidato na planilha do Google Sheets"""
    servico = build('sheets', 'v4', credentials=autenticar_google()) #constrói o serviço da API do Google Sheets


    planilha = servico.spreadsheets().get(
        spreadsheetId=id_planilha,
        fields="sheets(properties(sheetId,title))",
    ).execute() #obtém os dados da planilha


    aba = next(
        (
            item["properties"]
            for item in planilha.get("sheets", [])
            if item["properties"].get("sheetId") == 0
        ),
        None,
    ) #tenta encontrar a aba com o ID 0 (primeira aba da planilha)


    if not aba: #se não encontrar a aba com ID 0
        raise ValueError("Aba principal não encontrada na planilha.") #lança um erro informando que a aba não foi encontrada


    titulo_aba = str(aba["title"]).replace("'", "''")
    intervalo = f"'{titulo_aba}'!A1:Z1"


    resposta = servico.spreadsheets().values().get(
        spreadsheetId=id_planilha,
        range=intervalo,
    ).execute() #obtém os valores das células no intervalo definido


    linhas = resposta.get("values", []) #obtém os valores das linhas da planilha
    cabecalho = linhas[0] if linhas else [] #obtém o cabeçalho da planilha (primeira linha)
    # Aceita variações de cabeçalho como "Nome Candidato" e "Data Entrevista".
    def normalizar_cabecalho(valor):
        texto = unicodedata.normalize("NFKD", str(valor))
        texto = "".join(letra for letra in texto if not unicodedata.combining(letra))
        return "".join(letra for letra in texto.lower() if letra.isalnum())

    colunas = [normalizar_cabecalho(coluna) for coluna in cabecalho]


    dados = {
        "nome" : nome,
        "email" : email,
        "vaga" : vaga,
        "status" : status,
    }


    if data_hora_inicio:
        try:
            inicio = datetime.fromisoformat(data_hora_inicio)
            if any(coluna in CAMPOS_DA_PLANILHA["data"] for coluna in colunas):
                dados["data"] = inicio.strftime("%d/%m/%Y")
            if any(coluna in CAMPOS_DA_PLANILHA["hora"] for coluna in colunas):
                hora = inicio.strftime("%H:%M")
                if data_hora_fim:
                    fim = datetime.fromisoformat(data_hora_fim)
                    hora += f" - {fim.strftime('%H:%M')}"
                dados["hora"] = hora
            if any(coluna in CAMPOS_DA_PLANILHA["data_hora_inicio"] for coluna in colunas):
                dados["data_hora_inicio"] = inicio.strftime("%d/%m/%Y %H:%M")
            if data_hora_fim and any(coluna in CAMPOS_DA_PLANILHA["data_hora_fim"] for coluna in colunas):
                fim = datetime.fromisoformat(data_hora_fim)
                dados["data_hora_fim"] = fim.strftime("%d/%m/%Y %H:%M")
        except ValueError as erro:
            raise ValueError("Formato de data e hora inválido.") from erro

    # Monta a linha da planilha em ordem de colunas
    valores = []
    for coluna in colunas:
        campo = next(
            (nome_campo for nome_campo, aliases in CAMPOS_DA_PLANILHA.items() if coluna in aliases),
            None,
        )
        valores.append(dados.get(campo, "") if campo else "")

    # Se a planilha estiver vazia, cria o cabeçalho padrão antes de inserir dados.
    if not colunas:
        colunas = ["nome", "email", "vaga", "status", "data", "hora"]
        servico.spreadsheets().values().update(
            spreadsheetId=id_planilha,
            range=f"'{titulo_aba}'!A1:F1",
            valueInputOption="USER_ENTERED",
            body={"values": [colunas]},
        ).execute()
        valores = [dados.get(campo, "") for campo in colunas]

    resultado = servico.spreadsheets().values().append(
        spreadsheetId=id_planilha,
        range=intervalo,
        valueInputOption="USER_ENTERED",
        body={"values": [valores]},
    ).execute()

    return "candidato registrado com sucesso"

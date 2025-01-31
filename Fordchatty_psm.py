import os
from langchain.chains import RetrievalQA
from langchain.llms import OpenAI
from langchain.document_loaders import TextLoader
from langchain.document_loaders import PyPDFLoader
from langchain.indexes import VectorstoreIndexCreator
from langchain.text_splitter import CharacterTextSplitter
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma
import tempfile
import urllib.request
import fitz
import re
import numpy as np
import tensorflow_hub as hub
import openai
import gradio as gr
import os
from sklearn.neighbors import NearestNeighbors

def download_pdf(url, output_path):
    urllib.request.urlretrieve(url, output_path)


def preprocess(text):
    text = text.replace('\n', ' ')
    text = re.sub('\s+', ' ', text)
    return text


def pdf_to_text(path, start_page=1, end_page=None):
    doc = fitz.open(path)
    total_pages = doc.page_count

    if end_page is None:
        end_page = total_pages

    text_list = []

    for i in range(start_page-1, end_page):
        text = doc.load_page(i).get_text("text")
        text = preprocess(text)
        text_list.append(text)

    doc.close()
    return text_list


def text_to_chunks(texts, word_length=150, start_page=1):
    text_toks = [t.split(' ') for t in texts]
    page_nums = []
    chunks = []

    for idx, words in enumerate(text_toks):
        for i in range(0, len(words), word_length):
            chunk = words[i:i+word_length]
            if (i+word_length) > len(words) and (len(chunk) < word_length) and (
                len(text_toks) != (idx+1)):
                text_toks[idx+1] = chunk + text_toks[idx+1]
                continue
            chunk = ' '.join(chunk).strip()
            chunk = f'[Page no. {idx+start_page}]' + ' ' + '"' + chunk + '"'
            chunks.append(chunk)
    return chunks


class SemanticSearch:

    def __init__(self):
        self.use = hub.load('https://tfhub.dev/google/universal-sentence-encoder/4')
        self.fitted = False


    def fit(self, data, batch=1000, n_neighbors=5):
        self.data = data
        self.embeddings = self.get_text_embedding(data, batch=batch)
        n_neighbors = min(n_neighbors, len(self.embeddings))
        self.nn = NearestNeighbors(n_neighbors=n_neighbors)
        self.nn.fit(self.embeddings)
        self.fitted = True


    def __call__(self, text, return_data=True):
        inp_emb = self.use([text])
        neighbors = self.nn.kneighbors(inp_emb, return_distance=False)[0]

        if return_data:
            return [self.data[i] for i in neighbors]
        else:
            return neighbors


    def get_text_embedding(self, texts, batch=1000):
        embeddings = []
        for i in range(0, len(texts), batch):
            text_batch = texts[i:(i+batch)]
            emb_batch = self.use(text_batch)
            embeddings.append(emb_batch)
        embeddings = np.vstack(embeddings)
        return embeddings

def qa(file, query, chain_type, k):
    # load document
    loader = PyPDFLoader(file)
    documents = loader.load()
    # split the documents into chunks
    text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
    texts = text_splitter.split_documents(documents)
    # select which embeddings we want to use
    embeddings = OpenAIEmbeddings()
    # create the vectorestore to use as the index
    db = Chroma.from_documents(texts, embeddings)
    # expose this index in a retriever interface
    retriever = db.as_retriever(search_type="similarity", search_kwargs={"k": k})
    # create a chain to answer questions
    qa = RetrievalQA.from_chain_type(
        llm=OpenAI(), chain_type=chain_type, retriever=retriever, return_source_documents=True)
    result = qa({"query": query})
    print(result['result'])
    return result

def compute_doc_embeddings(text, progress):
    result = {}
    for idx in progress.tqdm(range(len(text))):
        try:
            res = get_embedding(text[idx].page_content)
        except:
            done = False
            while not done:
                sleep(5)
                try:
                    res = get_embedding(text[idx].page_content)
                    done = True
                except:
                    pass
        result[idx] = res

    return result

def load_recommender(path, start_page=1):
    global recommender
    texts = pdf_to_text(path, start_page=start_page)
    chunks = text_to_chunks(texts, start_page=start_page)
    recommender.fit(chunks)
    return 'Corpus Loaded.'


def generate_text(openAI_key,prompt, engine="text-davinci-003"):
    openai.api_key = openAI_key
    completions = openai.Completion.create(
        engine=engine,
        prompt=prompt,
        max_tokens=512,
        n=1,
        stop=None,
        temperature=0.7,
    )
    message = completions.choices[0].text
    return message


def generate_answer(question,openAI_key):
    topn_chunks = recommender(question)
    prompt = ""
    prompt += 'search results:\n\n'
    for c in topn_chunks:
        prompt += c + '\n\n'

    prompt += "Instructions: Compose a comprehensive reply to the query using the search results given. "\
              "Cite each reference using [ Page Number] notation (every result has this number at the beginning). "\
              "Citation should be done at the end of each sentence. If the search results mention multiple subjects "\
              "with the same name, create separate answers for each. Only include information found in the results and "\
              "don't add any additional information. Make sure the answer is correct and don't output false content. "\
              "If the text does not relate to the query, simply state 'Found Nothing'. Ignore outlier "\
              "search results which has nothing to do with the question. Only answer what is asked. The "\
              "answer should be short and concise. \n\nQuery: {question}\nAnswer: "

    prompt += f"Query: {question}\nAnswer:"
    answer = generate_text(openAI_key, prompt,"text-davinci-003")
    return answer


def question_answer(url, file, question,openAI_key):
    if openAI_key.strip()=='':
        return '[ERROR]: Please enter you Open AI Key. Get your key here : https://platform.openai.com/account/api-keys'
    if url.strip() == '' and file == None:
        return '[ERROR]: Both URL and PDF is empty. Provide atleast one.'

    if url.strip() != '' and file != None:
        return '[ERROR]: Both URL and PDF is provided. Please provide only one (eiter URL or PDF).'

    if url.strip() != '':
        glob_url = url
        download_pdf(glob_url, 'corpus.pdf')
        load_recommender('corpus.pdf')

    else:
        old_file_name = file.name
        file_name = file.name
        file_name = file_name[:-12] + file_name[-4:]
        os.rename(old_file_name, file_name)
        load_recommender(file_name)

    if question.strip() == '':
        return '[ERROR]: Question field is empty'

    return generate_answer(question,openAI_key)


recommender = SemanticSearch()

title = '😊 Chat with Ford-Chatty ©'
description = """ Ford Chatty allows you to chat with your PDF file using Open AI. The returned response can even cite the page number in square brackets([]) where the information is located, adding credibility to the responses."""


with gr.Blocks(css=".gradio-container {background-color: lightgray}") as demo:

    gr.Markdown(f'<center><h1>{title}</h1></center>')
    gr.Markdown(description)

    with gr.Row():

        with gr.Group():
            gr.Markdown(f'<p style="text-align:center">Get your Open AI API key <a href="https://platform.openai.com/account/api-keys">here</a></p>')
            openAI_key=gr.Textbox(label='Paste your OpenAI API key (sk-...) and hit Enter', show_label=False, lines=1, type="password")
            url = gr.Textbox(label='Enter PDF URL here')
            gr.Markdown("<center><h4>OR<h4></center>")
            file = gr.File(label='Upload your PDF/ Research Paper / Book here', file_types=['.pdf'])
            question = gr.Textbox(label='Enter your question here')
            btn = gr.Button(value='Submit')
            btn.style(full_width=True)


        with gr.Group():
            answer = gr.Textbox(label='The answer to your question is :')
            btn.click(question_answer, inputs=[url, file, question,openAI_key], outputs=[answer])
            gr.Markdown("<center><h4>Need Further Help?<h4></center>")
            gr.HTML("""
            <p>Click here on <a href='https://www.ford.com/support/vehicle/mustang-mach-e/2022/discover-your-ford/'>Discover Your Ford</a>,
            for further help on your vehicle.
            </p>""")


        with gr.Accordion("General examples of Questions to Ask", open=False):
            gr.Examples(
                examples=["What is the standard tyre pressure that I need to maintain?",
                          "How to start the vehicle?",
                          "Where can I find Cruise control?",
                          "Can you help me understand the gears of automatic transmission",
                          "What is child safety seat belt extension?",
                          "How to install recovery hook?",
                          "How to check engine Oil?",
                          "What is covered under vehicle warranty?"],
                inputs=question
            )


#openai.api_key = os.getenv('Your_Key_Here')
demo.launch()
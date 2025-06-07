import json
import nltk
from nltk.stem import WordNetLemmatizer
from nltk.tokenize import word_tokenize
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.naive_bayes import MultinomialNB
import numpy as np
import pickle

# Ensure nltk data path is set before tokenization
nltk.data.path.append(r"C:\Users\srikr\AppData\Roaming\nltk_data")

# Download required data (optional here)
nltk.download('punkt')
nltk.download('wordnet')
nltk.download('omw-1.4')

lemmatizer = WordNetLemmatizer()

# Load intents JSON
try:
    with open('intents.json', 'r') as file:
        data = json.load(file)
except FileNotFoundError:
    print("intents.json file not found.")
    exit()

corpus = []
labels = []

# Prepare corpus and labels
for intent in data['intents']:
    for pattern in intent['patterns']:
        tokens = word_tokenize(pattern)
        tokens = [lemmatizer.lemmatize(word.lower()) for word in tokens]
        corpus.append(' '.join(tokens))
        labels.append(intent['tag'])

# Vectorize corpus
vectorizer = CountVectorizer()
X = vectorizer.fit_transform(corpus)
y = np.array(labels)

# Train model
model = MultinomialNB()
model.fit(X, y)

# Save model and vectorizer
with open('intent_model.pkl', 'wb') as f:
    pickle.dump((model, vectorizer), f)

print("Model training and saving completed.")

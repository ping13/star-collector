import sys
import logging
import diskcache
from transformers import pipeline, AutoTokenizer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MODEL= "Ateeqq/news-title-generator"
pipe = pipeline(
    "summarization",
    model=MODEL,
)

# use cache to save time fro subseuent runs
cache = diskcache.Cache("./title-generator.cache")

@cache.memoize()
def extract_title(text):
    logger.debug(f"Processing text of length: {len(text)}")
    min_length = 20
    max_length = 40

    # Check the length of the text and only proceed only, if the text is
    # sufficently long enough to justify a title creation
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    tokens = tokenizer.tokenize(text, max_length=max_length, truncation=True)  # Get tokenized text
    num_tokens = len(tokens)  # Count tokens
    logger.debug(f"Number of tokens: {num_tokens}")
    
    if num_tokens < min_length:
        logger.info(f"Text too short for title generation, returning original text: {text}")
        return text.replace("\n", " ")
    
    # Generate summary    
    logger.debug("Generating title using pipeline")
    result = pipe(text, min_length=10, max_length=20)
    if len(result) == 0:
        logger.warning("Pipeline returned empty result, using fallback")
        return text[80].replace("\n", " ")  # this is arbitrarily chosen
    else:
        title = result[0]['summary_text'].replace("\n", " ")
        logger.info(f"Generated title: {title}")
        return title


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        
    text = sys.stdin.read().strip()
    logger.debug("Reading input text from stdin")
    print(extract_title(text))

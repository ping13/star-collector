import sys
import logging
import diskcache
from transformers import pipeline, AutoTokenizer
from functools import lru_cache

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MODEL= "Ateeqq/news-title-generator"

# use cache to save time fro subseuent runs
cache = diskcache.Cache("./title-generator.cache")


@lru_cache(maxsize=1)
def get_tokenizer():
    return AutoTokenizer.from_pretrained(MODEL)


@lru_cache(maxsize=1)
def get_pipeline():
    tasks = ("summarization", "text2text-generation")
    last_error = None
    for task in tasks:
        try:
            return pipeline(task, model=MODEL)
        except KeyError as exc:
            last_error = exc
            logger.warning("Pipeline task %s unavailable, trying fallback", task)
        except Exception as exc:
            last_error = exc
            logger.warning("Failed to initialize %s pipeline: %s", task, exc)

    raise RuntimeError(f"Could not initialize title generation pipeline for {MODEL}") from last_error


def fallback_title(text: str) -> str:
    normalized = " ".join(text.replace("\n", " ").split())
    return normalized[:80]

@cache.memoize()
def extract_title(text):
    logger.debug(f"Processing text of length: {len(text)}")
    min_length = 20
    max_length = 40

    # Check the length of the text and only proceed only, if the text is
    # sufficently long enough to justify a title creation
    tokenizer = get_tokenizer()
    tokens = tokenizer.tokenize(text, max_length=max_length, truncation=True)  # Get tokenized text
    num_tokens = len(tokens)  # Count tokens
    logger.debug(f"Number of tokens: {num_tokens}")
    
    if num_tokens < min_length:
        logger.info(f"Text too short for title generation, returning original text: {text}")
        return text.replace("\n", " ")
    
    # Generate summary    
    logger.debug("Generating title using pipeline")
    try:
        pipe = get_pipeline()
        result = pipe(text, min_length=10, max_length=20)
    except Exception as exc:
        logger.warning("Title generation failed, using fallback title: %s", exc)
        return fallback_title(text)
    if len(result) == 0:
        logger.warning("Pipeline returned empty result, using fallback")
        return fallback_title(text)
    else:
        summary = result[0].get('summary_text') or result[0].get('generated_text') or ""
        title = summary.replace("\n", " ").strip() or fallback_title(text)
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

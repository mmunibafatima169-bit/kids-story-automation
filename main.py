from story_generator import generate_story
from gtts import gTTS

def main():
    topic = "a brave little rabbit"
    story = generate_story(topic)
    print("📖 Story generated:")
    print(story)

    with open("story.txt", "w") as f:
        f.write(story)
    print("✅ Story saved to story.txt")

    tts = gTTS(text=story, lang='en')
    tts.save("story.mp3")
    print("🎤 Story converted to speech (story.mp3)")

if __name__ == "__main__":
    main()

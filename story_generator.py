def generate_story(topic):
    stories = {
        "a brave little rabbit": """
        Once upon a time, in a green forest, lived a little rabbit named Hoppy. One day, Hoppy found a lost baby squirrel. "Don't worry!" said Hoppy. Together, they hopped and scampered until they found Mama Squirrel! The end. Sweet dreams! 🌙
        """,
        "a magical unicorn": """
        In a land of rainbows, lived a unicorn named Luna. Luna had a golden horn that could make flowers bloom. One day, Luna helped a lost kitten find its way home. The end. Sweet dreams! 🌙
        """
    }
    return stories.get(topic, f"A story about {topic}.")

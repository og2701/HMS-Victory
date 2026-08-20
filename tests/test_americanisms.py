from lib.core.americanisms import correct_americanisms

def test_corrections():
    test_cases = [
        ("I like this color.", "I like this colour."),
        ("The theater is in the center.", "The theatre is in the centre."),
        ("Favorite flavor!", "Favourite flavour!"),
        ("That's my COLOR.", "That's my COLOUR."),
        ("I love shopping cart and soccer.", "I love trolley and football."),
        ("y'all are crazy.", "you all are crazy."),
        ("ya'll are crazy.", "you all are crazy."),
        ("today all i did was sleep.", "today all i did was sleep."),
        ("why all the fuss?", "why all the fuss?"),
        ("Y'ALL ARE BRUZZ.", "YOU ALL ARE BROTHER."),
        ("i like the Centers.", "i like the Centres."),
        ("passed me the yogurt", "passed me the yoghurt"),
        ("two yogurts please", "two yoghurts please"),
        ("YOGURT", "YOGHURT"),
        ("Yogurt time", "Yoghurt time"),
        # already spelt properly - the filter must leave it alone
        ("passed me the yoghurt", "passed me the yoghurt"),
        # blocked by the rule but previously uncorrectable, so the message vanished
        ("let me analyze that rumor", "let me analyse that rumour"),
        ("do me a favor and organize it", "do me a favour and organise it"),
        ("i realized they organized it", "i realised they organised it"),
        ("y\u2019all ready?", "you all ready?"),
    ]
    
    for input_text, expected in test_cases:
        result = correct_americanisms(input_text)
        print(f"Input: {input_text}")
        print(f"Expected: {expected}")
        print(f"Result: {result}")
        assert result == expected, f"Failed for '{input_text}': expected '{expected}', got '{result}'"
        print("---")

if __name__ == "__main__":
    try:
        test_corrections()
        print("All local tests passed!")
    except AssertionError as e:
        print(f"Test failed: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

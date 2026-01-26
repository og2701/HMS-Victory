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

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        ("Y\u02bcall are cowards", "You all are cowards"),
        ("ya\u02bcll are cowards", "you all are cowards"),
        ("that is skibidi", "that is nonsense"),
        ("Skibibi toilet", "Nonsense toilet"),
        ("back to Iabor", "back to Labour"),
        # a word that merely contains a blocked one must be left alone
        ("laboratory research", "laboratory research"),
        ("concert tickets", "concert tickets"),
        # the families the list used to stop short of
        ("i apologize, i recognized it", "i apologise, i recognised it"),
        ("his behavior was marvelous", "his behaviour was marvellous"),
        ("we traveled a liter of fiber", "we travelled a litre of fibre"),
        ("the anesthetic maneuver", "the anaesthetic manoeuvre"),
        ("diagnosed with leukemia", "diagnosed with leukaemia"),
        ("mom canceled the vacation", "mum cancelled the holiday"),
        ("cozy pajamas and a donut", "cosy pyjamas and a doughnut"),
        # words that stay put: ordinary British English in another sense
        ("the program checks the draft", "the program checks the draft"),
        ("a gas meter on the first floor", "a gas meter on the first floor"),
        ("i tire of this practice", "i tire of this practice"),
        ("chips and jelly", "chips and jelly"),
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

"""Evaluation harness for pattern.py — DO NOT MODIFY.

Tests the regex pattern against 200 labeled email addresses.
Computes precision, recall, and F1 score.

Output format (parsed by crucible):
    f1_score: <float>
    precision: <float>
    recall: <float>
    tp: <int>
    fp: <int>
    fn: <int>
"""

import re
import sys
import time
import traceback

# 200 labeled email addresses (held-out, not shown to agent)
SAMPLES = [
    # Valid emails
    ("user@example.com", True),
    ("alice.bob@domain.org", True),
    ("test+tag@sub.example.co.uk", True),
    ("user123@company.net", True),
    ("first.last@university.edu", True),
    ("me@x.io", True),
    ("support@help-desk.com", True),
    ("no-reply@news.example.com", True),
    ("admin@192.168.0.1", True),
    ("user_name@domain.info", True),
    ("a@b.co", True),
    ("user@domain-name.com", True),
    ("hello@world.dev", True),
    ("test@test.test", True),
    ("u@domain.com", True),
    ("name+filter@gmail.com", True),
    ("123@numbers.com", True),
    ("contact@shop.store", True),
    ("info@company.io", True),
    ("dev@api.example.com", True),
    ("user.middle.last@domain.com", True),
    ("x+y+z@domain.org", True),
    ("test-1@domain-2.com", True),
    ("abc@xyz.museum", True),
    ("a1b2c3@d4e5.net", True),
    ("my.email@sub.domain.co.uk", True),
    ("user@domain.travel", True),
    ("noreply@auto.mailer.com", True),
    ("support+ticket-123@help.io", True),
    ("admin@10.0.0.1", True),
    ("user@domain.name", True),
    ("hello+world@foo.bar", True),
    ("simple@example.co", True),
    ("with_underscore@domain.com", True),
    ("hyphen-ok@domain.com", True),
    ("numbers123@domain456.com", True),
    ("dot.in.local@domain.com", True),
    ("plus+in+local@domain.com", True),
    ("mix_of-chars@domain.org", True),
    ("two@char.cc", True),
    ("user@sub1.sub2.example.com", True),
    ("a@a.aa", True),
    ("test@domain.solutions", True),
    ("user@company.global", True),
    ("x@y.z", True),
    ("email@123.123.123.123", True),
    ("1234567890@domain.com", True),
    ("email@domain.co.jp", True),
    ("email@subdomain.domain.com", True),
    ("firstname+lastname@domain.com", True),
    ("valid.email+suffix@domain.org", True),
    ("firstname.lastname@domain.com", True),
    ("email@domain.com", True),
    ("email@domain.info", True),
    ("email@domain.name", True),
    ("email@domain.mobi", True),
    ("email@domain.pro", True),
    ("email@domain.aero", True),
    ("email@domain.coop", True),
    ("email@domain.museum", True),
    ("test.email.with+symbol@domain.com", True),
    ("id-with-dash@domain.com", True),
    ("example-indeed@strange-domain.com", True),
    ("example.firstname.lastname@domain.com", True),
    ("try-this@domain.co.uk", True),
    ("send-here@domain.org.au", True),
    ("a+b@c.d", True),
    ("info+alerts@newsite.example.com", True),
    ("user.99@domain.com", True),
    ("user-00@domain-99.com", True),
    ("contact@my-company.net", True),
    ("hello.world@example.org", True),
    ("user+tag@domain.co", True),
    ("abc.def.ghi@domain.com", True),
    ("user@mail.domain.org", True),
    ("me+you@together.net", True),
    ("short@ab.cd", True),
    ("long.email.address.here@very-long-domain-name.com", True),
    ("user@domain.academy", True),
    ("no_dots@domain.com", True),
    ("multiple+plus+signs@domain.org", True),
    ("digits123+more@domain.net", True),
    ("user@sub.sub.sub.domain.com", True),
    ("first_last@domain.com", True),
    ("email@domain.technology", True),
    ("user@ip-domain.com", True),
    ("prefix.suffix@domain.co.uk", True),
    ("test123@test456.net", True),
    ("a@b.cc", True),
    ("user@domain.software", True),
    ("hello@world.finance", True),
    ("user@domain.app", True),
    ("test@domain.online", True),
    ("user@domain.email", True),
    ("simple.test@example.co", True),
    ("a.b.c@d.e.f", True),
    ("user@long-domain-name-here.com", True),
    ("email.with.dots@domain.org", True),
    # Invalid emails
    ("plainaddress", False),
    ("@missinglocal.com", False),
    ("user@", False),
    ("user@.com", False),
    ("user@domain..com", False),
    ("user name@domain.com", False),
    ("user@@domain.com", False),
    ("user@domain", False),
    (".user@domain.com", False),
    ("user.@domain.com", False),
    ("user@-domain.com", False),
    ("user@domain-.com", False),
    ("user@domain.c", False),
    ("()user@domain.com", False),
    ("user@domain@domain.com", False),
    ("user@dom ain.com", False),
    ("@", False),
    ("user@.", False),
    ("", False),
    ("just.a.string", False),
    ("missing@tld.", False),
    ("double..dot@domain.com", False),
    ("user @domain.com", False),
    (" user@domain.com", False),
    ("user@domain.com ", False),
    ("user@[invalid].com", False),
    ("user#tag@domain.com", False),
    ("user!name@domain.com", False),
    ("user$name@domain.com", False),
    ("user%name@domain.com", False),
    ("user^name@domain.com", False),
    ("user&name@domain.com", False),
    ("user*name@domain.com", False),
    ("user(name@domain.com", False),
    ("user)name@domain.com", False),
    ("user=name@domain.com", False),
    ("[user]@domain.com", False),
    ("{user}@domain.com", False),
    ("user|name@domain.com", False),
    ("user\\name@domain.com", False),
    ("user;name@domain.com", False),
    ("user:name@domain.com", False),
    ("user'name@domain.com", False),
    ('user"name@domain.com', False),
    ("user<name@domain.com", False),
    ("user>name@domain.com", False),
    ("user,name@domain.com", False),
    ("user?name@domain.com", False),
    ("user/name@domain.com", False),
    ("@domain.com", False),
    ("nodomain@", False),
    ("missingatsign.domain.com", False),
    ("missing@dot", False),
    ("two@@at.com", False),
    ("..double@domain.com", False),
    ("domain@.start.dot.com", False),
    ("domain@end.dot.com.", False),
    ("space in@domain.com", False),
    ("space@in domain.com", False),
    ("tab\tin@domain.com", False),
    ("newline\n@domain.com", False),
    ("@nodomain", False),
    ("no@tld", False),
    ("a@b.c1", False),
    ("a@b.cc3", False),
    ("a@b.1com", False),
    ("a@b.com1", False),
    ("local@-hyphen.com", False),
    ("local@hyphen-.com", False),
    ("local@.leading.com", False),
    ("local@trailing.com.", False),
    ("a@b", False),
    ("a@b.", False),
    (".@domain.com", False),
    ("a..b@domain.com", False),
    ("a@b..c", False),
    ("user@domain.123", False),
    ("no-at-sign.com", False),
    ("two@signs@domain.com", False),
    ("user@-startwithhyphen.com", False),
    ("user@endwithhyphen-.com", False),
    ("@.com", False),
    ("user@.domain.com", False),
    ("user@domain.com.", False),
    ("..@domain.com", False),
    ("user@domain..org", False),
    ("user@@domain.org", False),
    ("invalid#char@domain.com", False),
    ("space here@domain.com", False),
    ("user@domain .com", False),
    ("user@domain.c0m", False),
    ("user@domain.12", False),
    ("user@1.2.3", False),
    ("@example.com", False),
    ("user@", False),
    ("user@example..com", False),
    ("user..name@domain.com", False),
    ("user.@example.com", False),
    (".username@example.com", False),
    ("username@.example.com", False),
    ("username@example-.com", False),
    ("username@-example.com", False),
    ("user name@example.com", False),
    ("username@exam ple.com", False),
]


def check_catchall(pattern_str: str) -> bool:
    stripped = pattern_str.strip()
    return stripped in [".*", ".+", r"\S+", r"\w+", r".+@.+", r"\S+@\S+"]


def main():
    try:
        import importlib.util
        spec_mod = importlib.util.spec_from_file_location("pattern", "pattern.py")
        mod = importlib.util.module_from_spec(spec_mod)
        spec_mod.loader.exec_module(mod)
        pattern_str = mod.PATTERN

        if check_catchall(pattern_str):
            print("VIOLATION: catch-all pattern not allowed")
            print("f1_score: 0.0")
            return

        compiled = re.compile(pattern_str)

        t0 = time.perf_counter()
        results = []
        for text, label in SAMPLES:
            matched = bool(compiled.fullmatch(text))
            results.append((matched, label))
        elapsed = time.perf_counter() - t0

        if elapsed > 2.0:
            print(f"TIMEOUT: pattern matching took {elapsed:.2f}s > 2s limit")
            print("f1_score: 0.0")
            return

        tp = sum(1 for m, l in results if m and l)
        fp = sum(1 for m, l in results if m and not l)
        fn = sum(1 for m, l in results if not m and l)
        tn = sum(1 for m, l in results if not m and not l)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)

        print(f"f1_score: {f1:.4f}")
        print(f"precision: {precision:.4f}")
        print(f"recall: {recall:.4f}")
        print(f"tp: {tp}")
        print(f"fp: {fp}")
        print(f"fn: {fn}")
        print(f"tn: {tn}")
        print(f"match_time_ms: {elapsed * 1000:.2f}")

    except re.error as e:
        print(f"ERROR: invalid regex: {e}")
        print("f1_score: 0.0")
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("f1_score: 0.0")


if __name__ == "__main__":
    main()

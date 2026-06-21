def f():
    x = 10
    l = lambda: x
    x += 5
    print(l())
f()

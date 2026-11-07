"""The level intro: what the level teaches and what to watch, in Spanish, shown before the level
until the player hits the snare (S turns it off; `App.intro_on`). One entry per level name in
INTROS: a paragraph ("qué vamos a aprender") and a few notable points. `intro_for` adds the
level's facts (tempo, compases, subdivisión, cuerpos, qué se juzga) from the chart itself, so
a level without an entry still gets a screen. `python -m bathero.intro [NAME ...]` prints
them and names the levels without an entry (`missing()`)."""
import re

from . import chart as C

# name -> (qué vamos a aprender, [para tener en cuenta, ...]). Right and left are written as
# "derecha" / "izquierda" and swapped for the left-hand-lead version (Chart.lead == "L").
INTROS = {
    # --- Beats ------------------------------------------------------------------------------
    "1 · Money beat": (
        "El beat más tocado del mundo: bombo en 1 y 3, redoblante en 2 y 4, hi-hat en cada negra. "
        "Es la base de todo lo que viene después.",
        ["La mano derecha marca el pulso en el hi-hat: es la referencia, no la acompañante.",
         "Bombo y hi-hat suenan juntos en el 1 y el 3; redoblante y hi-hat en el 2 y el 4. Que caigan exactamente a la vez."]),
    "2 · Eighth-note hats": (
        "El mismo beat con el hi-hat en corcheas: la mano derecha pasa a llevar el tiempo con el doble de golpes.",
        ["Las corcheas del hi-hat tienen que ser parejas, sin acentuar los tiempos por reflejo.",
         "El bombo y el redoblante no cambian: si se desarman, la mano derecha está arrastrándolos."]),
    "3 · Kick on the &": (
        "Un segundo bombo en el & de 3, y en el compás de respuesta en el & de 1: el bombo empieza a caer entre las corcheas del hi-hat.",
        ["El bombo del & cae junto con un hi-hat, nunca entre dos.",
         "Son dos compases distintos que se alternan: escuchá dónde está el bombo extra en cada uno."]),
    "4 · Four-bar phrase": (
        "Tres compases de groove y un cuarto con una anticipación del redoblante en el & de 4: la frase toma forma de cuatro compases.",
        ["La anticipación del & de 4 es un golpe suave, no un acento: lleva al 1 sin taparlo.",
         "Contá los compases: el cuarto es distinto y hay que verlo venir."]),
    "5 · Crash on the one": (
        "El crash izquierdo reemplaza al hi-hat en el 1 que abre cada frase de cuatro compases.",
        ["El crash cae con el bombo, y la mano vuelve al hi-hat en la siguiente corchea sin perder una.",
         "Hay que llegar al crash: moviendo la mano antes, no apurando el golpe."]),
    "6 · First fill": (
        "El primer relleno: el compás 4 termina con semicorcheas en el redoblante en el tiempo 4 y cae directo en el crash.",
        ["Las cuatro semicorcheas van alternadas, derecha izquierda derecha izquierda, parejas.",
         "El hi-hat para donde empieza el relleno y el crash del 1 vuelve al groove: no se pierde el pulso en el cambio."]),
    "7 · Rack tom enters": (
        "El relleno se mueve: dos redoblantes y dos toms. Aparece el carril del tom.",
        ["Dos golpes en cada tambor: el cambio de tambor es a mitad del tiempo 4.",
         "Buscá el tom con la vista antes de llegar, la mano sigue el ojo."]),
    "8 · Floor tom enters": (
        "El relleno baja por los tambores: redoblante, tom, chancha, sobre los tiempos 3 y 4. Aparece la chancha.",
        ["Empieza en el 3, un tiempo antes que los anteriores: el hi-hat corta más temprano.",
         "Ocho semicorcheas seguidas repartidas en tres tambores: mantené la velocidad pareja en el cambio."]),
    "9 · Toms in the groove": (
        "Los toms entran al groove: la chancha hace el trabajo del hi-hat en 1 y 3, y el tom responde en el & de 4.",
        ["La mano derecha va y viene entre chancha y hi-hat dentro del mismo compás.",
         "El golpe del tom en el & de 4 es la respuesta de la frase: solo, sin bombo debajo."]),
    "10 · Ride": (
        "La mano derecha pasa al ride en la segunda mitad de la frase, con un crash a la entrada.",
        ["El ride lleva las mismas corcheas que el hi-hat: cambia el sonido, no el patrón.",
         "El crash marca el cambio de sección: es el momento de mover la mano al ride."]),
    "11 · Two crashes": (
        "Dos crashes: el izquierdo abre la frase, el derecho responde en el 3 del compás 4 y la cierra.",
        ["Ocho compases, el ride lleva todo el tiempo.",
         "El crash derecho del compás 4 cae en medio del relleno: el relleno lo prepara y sigue después."]),
    "12 · Half time": (
        "Medio tiempo: redoblante solo en el 3, bombo en 1 y en el & de 2. El doble de espacio con la misma forma de frase.",
        ["A 80 bpm la corchea del hi-hat es lenta: no la apures, el espacio es el punto.",
         "El redoblante en 3 tiene que sonar como el backbeat de un compás grande, no como un 2 y 4 a la mitad."]),
    "13 · Sixteenth kicks": (
        "El bombo cae en el e y en el a: el bolsillo funk debajo de los hi-hats.",
        ["Los bombos en semicorchea caen entre los golpes del hi-hat, no con ellos: pie y mano se cruzan.",
         "Que el pie no arrastre a la mano: las corcheas del hi-hat siguen iguales."]),
    "14 · Ghost notes": (
        "Notas fantasma: redoblantes suaves en el e de 2 y en el a de 3, entre los backbeats.",
        ["Los fantasmas se tocan con la izquierda, bajito, la punta apenas levantada: si suenan como el 2 y el 4, están fuertes.",
         "El contraste es la nota: el 2 y el 4 fuertes, lo demás casi inaudible."]),
    "15 · Sixteenth hats": (
        "Una mano toca todas las semicorcheas en el hi-hat mientras el bolsillo funk se queda quieto.",
        ["Dieciséis golpes por compás con una sola mano: relajá la muñeca, es rebote más que fuerza.",
         "El bombo en el e y el a cae debajo de un hi-hat esta vez, no entre dos."]),
    "16 · Linear": (
        "Groove lineal: nada suena junto. Hi-hat, bombo y redoblante se turnan, y toms y ride llenan los huecos.",
        ["Cada semicorchea es de un solo cuerpo: si dos suenan a la vez, hay un error.",
         "El pulso lo arma la suma de todos: no hay mano que lleve el tiempo, hay que sentirlo."]),
    "17 · Song form": (
        "Forma de canción A A B A: estrofas en el hi-hat, un puente en el ride con toms, dos crashes, notas fantasma, todo junto.",
        ["Dieciséis compases: anticipá cada cambio de sección, están cada cuatro.",
         "Todo lo de los niveles anteriores en una sola pieza: si algo se cae, ese es el nivel para volver."]),
    "18 · Fill vocabulary": (
        "Vocabulario de rellenos: uno distinto cada dos compases. Semicorcheas en el redoblante, pares redoblante-tom, la bajada, y los cuatro tambores.",
        ["Cada relleno ocupa el tiempo 4 (o el 3 y el 4) y termina en el crash del 1.",
         "Cuatro rellenos distintos: mirá el carril de los toms para saber cuál viene."]),
    "19 · Kick doubles": (
        "Dobles de bombo: dos bombos seguidos en semicorcheas con un solo pedal, en el & a de 1 y en el a de 3.",
        ["Con un solo pedal el doble sale del rebote del talón o de la punta, no de dos pisadas separadas.",
         "El segundo bombo del doble cae justo antes del 2: que no lo adelante."]),
    "20 · Punk": (
        "Punk: redoblante en cada &, bombo en cada tiempo, hi-hat acompañando. El redoblante a contratiempo lo empuja.",
        ["El redoblante nunca cae en el tiempo: si lo sentís en el 2 y el 4, se dio vuelta.",
         "Bombo y hi-hat juntos en cada tiempo, redoblante y hi-hat juntos en cada &."]),
    "21 · Gallop": (
        "El galope del metal en el bombo: corchea y dos semicorcheas en cada tiempo bajo un hi-hat derecho.",
        ["Corchea, semicorchea, semicorchea: el hueco está después del primer golpe, no antes.",
         "Doce bombos por compás con un pie: cuidá que las semicorcheas no se abran."]),
    "22 · Riding the crash": (
        "En la segunda mitad de la frase la mano derecha lleva las corcheas en el crash izquierdo; el crash derecho marca la vuelta.",
        ["El crash en corcheas se toca más suave que un crash de acento: es un lavado, no ocho golpes.",
         "El crash derecho es un solo golpe y marca la vuelta al hi-hat."]),
    "23 · Crash on the &": (
        "El crash derecho cae en el & de 4 con un bombo debajo, empujando hacia el compás siguiente.",
        ["El crash del & cae antes de la línea del compás: es una anticipación, y el 1 que sigue va sin crash.",
         "Bombo y crash a la vez en el &: pie y mano juntos, fuera del tiempo."]),
    "24 · Kick runs": (
        "Corridas de bombo en semicorcheas en el compás 4: primero medio compás, después uno entero debajo del redoblante.",
        ["Semicorcheas seguidas con un solo pie: rebote, la pierna relajada.",
         "El backbeat sigue mientras el pie corre: las manos no se contagian del pie."]),
    "25 · Around the kit": (
        "Rellenos de un compás entero cada dos: del redoblante al tom y a la chancha, de vuelta, en pares, y con bombos.",
        ["Un compás entero de relleno: el 1 del compás siguiente es el ancla, contá hasta llegar.",
         "El último relleno mezcla bombos: manos y pie alternan dentro del mismo dibujo."]),
    "26 · Breakdown": (
        "Breakdown: medio tiempo, crash derecho en cada tiempo, redoblante en 3, y el bombo picando las semicorcheas abajo.",
        ["Cuatro crashes por compás, todos iguales: pesados, sin apurar.",
         "El bombo tiene el dibujo: escuchalo contra el crash, que cae parejo."]),
    "27 · Three over four": (
        "Tres contra cuatro: el bombo camina en grupos de tres semicorcheas a lo largo del compás mientras el hi-hat y el backbeat siguen derechos.",
        ["Los grupos de tres se corren respecto del tiempo: el bombo cae una semicorchea más tarde en cada tiempo.",
         "Las manos son el ancla: si se desarman, el pie se llevó todo."]),
    "28 · Blast, lite": (
        "Blast beat liviano: bombo y redoblante alternan en las corcheas bajo un ride en corcheas. Subile la velocidad cuando se asiente.",
        ["Bombo y redoblante nunca juntos: uno en el tiempo, otro en el &, siempre.",
         "El ride cae con los dos: la mano derecha es el metrónomo."]),
    "29 · Crash landing": (
        "Rellenos que terminan en un crash con bombo, y el crash derecho que responde en el & antes de la frase siguiente.",
        ["El crash de aterrizaje va con el bombo, en el 1: es el final del relleno, tocalo entero.",
         "El & que responde es otro crash: contá para no adelantarlo."]),
    "30 · Rock anthem": (
        "Himno de rock en dieciséis compases: estrofa en el hi-hat, un build con el redoblante creciendo de fantasmas a acentos, estribillo en el crash, final grande.",
        ["El build es dinámico: cada compás del redoblante un poco más fuerte, no todos iguales.",
         "El estribillo lava el crash en corcheas: mismo patrón que la estrofa, otro cuerpo."]),
    "31 · Cumbia": (
        "Cumbia villera en los cuerpos: chancha en 1 y 3, tom en cada &, redoblante en 2 y 4, con anticipaciones en el tom y en la chancha.",
        ["Sin bombo ni hi-hat: la base entera son los tambores.",
         "El tom en cada & es el que da el balanceo: parejo y liviano.",
         "La pista de fondo es una cumbia: escuchala, el güiro marca el mismo &."]),
    "32 · Cumbia: repiques": (
        "La cumbia del nivel 31 con los repiques del redoblante: arrastres fantasma hacia el backbeat, corridas en semicorcheas saliendo del 4 y entrando en él, y un repique de dos tiempos cerrando la frase.",
        ["Los repiques son cortos y picados: cada golpe suena separado, sin redoble.",
         "El arrastre hacia el 2 y el 4 es suave: es un adorno, el backbeat sigue mandando."]),
    "33 · Reggae: one drop, fills": (
        "Reggae one drop: bombo y aro en el 3, hi-hat en corcheas, sin nada en el 1. Cada cuarto compás un relleno clásico: cuatro en el 4, la bajada por los toms, dobles del redoblante hacia la chancha, y el redoble sobre 3 y 4.",
        ["El 1 está vacío: lo más difícil del one drop es no tocarlo.",
         "El golpe del 3 es aro y bombo juntos: pesado y seco.",
         "Cuatro rellenos distintos, uno cada cuatro compases: mirá cuál viene."]),

    # --- Exercises ------------------------------------------------------------------------
    "Kick on the beat": (
        "Bombo en cada tiempo. El nivel más simple: es para sentir dónde está la línea y cómo se juzga un golpe.",
        ["Mirá la nota llegar a la línea y tocá justo ahí: el juicio (PERFECT, GOOD, OK) te dice cuánto te corriste.",
         "Si todo te sale tarde o temprano, el offset de latencia se ajusta con , y ."]),
    "Snare on the beat": (
        "Redoblante en cada tiempo.",
        ["Golpes iguales, del centro del parche, todos con la misma fuerza.",
         "Cuatro por compás, ocho compases: es una calibración, no un ejercicio de resistencia."]),
    "Snare on 2 and 4": (
        "Solo el backbeat: redoblante en 2 y 4.",
        ["Hay que contar el 1 y el 3 en silencio: el metrónomo los marca, vos no.",
         "Dieciséis golpes en total: cada uno cuenta mucho en la nota."]),
    "Hi-hat on the beat": (
        "Hi-hat en cada tiempo.",
        ["Pedal apretado, el golpe en el cuerpo del platillo (no en el borde).",
         "Es el hi-hat: lo que se juzga es el tiempo, no la apertura."]),
    "Crash on the beat": (
        "Crash en cada tiempo, dejándolo sonar.",
        ["Cuatro compases nada más: golpes amplios, sin ahogar el platillo.",
         "El crash se toca con el filo de la baqueta cruzando el borde, no clavado."]),
    "Kick and snare": (
        "Bombo en 1 y 3, redoblante en 2 y 4: el esqueleto de todos los beats.",
        ["Es el money beat sin hi-hat: pie y mano se alternan, nunca juntos.",
         "Que el redoblante no se adelante después del bombo: el 2 está tan lejos del 1 como el 3 del 2."]),
    "Alternating": (
        "Bombo, redoblante, bombo, redoblante: alternando en cada tiempo, un poco más rápido.",
        ["Mismo patrón que el anterior a 85 bpm.",
         "Pie y mano parejos: el bombo suele salir tarde respecto de la mano."]),
    "Hi-hat eighths": (
        "Hi-hat en cada corchea: la mano derecha llevando el tiempo sola.",
        ["Sesenta y cuatro golpes seguidos: relajá el brazo, el movimiento sale de la muñeca.",
         "Sin acentuar los tiempos: todas las corcheas iguales."]),
    "Single strokes 8ths": (
        "Golpes simples en corcheas: derecha, izquierda, alternando. El rudimento más básico, con acento en el tiempo.",
        ["Se juzgan las dinámicas: el golpe del tiempo es un acento (fuerte) y el del & un tap (suave).",
         "Las dos manos tienen que sonar iguales: el tap de la izquierda como el de la derecha."]),
    "Single strokes 16ths": (
        "Golpes simples en semicorcheas, alternando, con acento en el tiempo.",
        ["Cuatro golpes por tiempo, el primero acentuado: el acento cae siempre en la derecha.",
         "Los tres taps entre acentos van bajos y parejos: el contraste es lo que se mide."]),
    "Paradiddle": (
        "El paradiddle: derecha izquierda derecha derecha, izquierda derecha izquierda izquierda, con acento en el primer golpe de cada grupo.",
        ["El acento alterna de mano en cada tiempo: derecha en el 1, izquierda en el 2.",
         "Los dobles (derecha derecha, izquierda izquierda) van suaves y parejos: el segundo golpe del doble suele salir más bajo o más tarde.",
         "Los corchetes en el carril marcan cada doble del lado de la mano que lo toca."]),
    "Paradiddle hat / snare": (
        "El paradiddle repartido: la mano derecha en el hi-hat y la izquierda en el redoblante.",
        ["Mismo sticking que el paradiddle, pero cada mano tiene su cuerpo: el dibujo se escucha entre los dos.",
         "El acento del 1 es en el hi-hat, el del 2 en el redoblante."]),
    "Triplets": (
        "Tresillos de corchea alternando, con acento en el tiempo.",
        ["Tres golpes por tiempo: el acento cambia de mano en cada tiempo (derecha, izquierda, derecha...).",
         "El tresillo tiene que sonar redondo: tres golpes iguales, no una corchea con dos semicorcheas."]),
    "Triplets R L L": (
        "Derecha izquierda izquierda en cada tiempo: el patrón de manos del shuffle.",
        ["El acento va en la derecha, siempre en el tiempo; las dos izquierdas son un doble suave.",
         "La mano derecha lleva el pulso: si la sentís en el tiempo, el tresillo está bien puesto."]),
    "Double paradiddle": (
        "El doble paradiddle: derecha izquierda derecha izquierda derecha derecha, y al revés, en tresillos.",
        ["Seis golpes por dos tiempos: cuatro simples y un doble, el acento en el primero de cada seis.",
         "Como en el paradiddle el acento alterna de mano, pero cada dos tiempos."]),
    "Paradiddle-diddle": (
        "El paradiddle-diddle: derecha izquierda derecha derecha izquierda izquierda en tresillos, acento en el primero.",
        ["Seis golpes por dos tiempos: dos simples y dos dobles. El acento siempre cae en la derecha.",
         "El patrón no alterna de mano: la derecha lleva todos los acentos."]),
    "Six stroke roll in triplets": (
        "El redoble de seis golpes en tresillos: derecha izquierda izquierda derecha derecha izquierda sobre dos tiempos. Acento en los simples, dobles suaves.",
        ["Los acentos son el primero y el último golpe (los dos simples); los dos dobles del medio van suaves.",
         "El acento del final cae en el segundo tiempo con la izquierda: es el que suele salir flojo."]),
    "Six stroke roll": (
        "Los mismos seis golpes dentro de un tiempo: un seisillo, acentos en el primero y en el último.",
        ["A 60 bpm el seisillo va a 360 golpes por minuto: rebote en los dobles, sin forzar.",
         "El acento del último golpe y el del primero del tiempo siguiente están pegados: dos acentos seguidos, izquierda derecha.",
         "Tiene su propia pista de fondo: el arpegio cae en el seisillo, dejate llevar."]),
    "Six stroke roll R L R R L L": (
        "El seisillo con los simples primero: derecha izquierda derecha derecha izquierda izquierda. Los dos acentos caen juntos, después los dos dobles.",
        ["Los acentos son los dos primeros golpes: derecha e izquierda, fuertes y seguidos.",
         "Los cuatro golpes que siguen son dos dobles suaves: el seisillo va de fuerte a suave dentro de cada tiempo."]),
    "Sixteenths + six stroke roll": (
        "Semicorcheas en golpes simples, y en el tiempo 4 un redoble de seis golpes como seisillo.",
        ["La subdivisión cambia en el tiempo 4: de cuatro golpes a seis en el mismo lapso.",
         "Los acentos: uno por tiempo en las semicorcheas, y el último golpe del seisillo."]),
    "Paradiddle + six stroke roll": (
        "Paradiddle en semicorcheas y redoble de seis como seisillo, alternando tiempos y manos: la subdivisión cambia en cada tiempo.",
        ["Un tiempo de cuatro, un tiempo de seis, y así: contá el cambio de velocidad, no lo adivines.",
         "La mano que empieza cada tiempo alterna: el paradiddle deja la mano lista para el seisillo."]),
    "2 paradiddles, six stroke, 1 more": (
        "Dos paradiddles, un redoble de seis como seisillo, y un paradiddle más. El compás termina en la derecha, así que el siguiente empieza con la izquierda y todo se toca al revés.",
        ["Dos compases distintos: el segundo es el espejo del primero. Fijate qué mano empieza cada uno.",
         "El seisillo está en el tiempo 3: llega después de dos paradiddles, no al final."]),
    "Double paradiddle + six stroke": (
        "Todo en seisillos: un doble paradiddle y después un redoble de seis, alternando tiempos y manos.",
        ["Un tiempo de doble paradiddle, un tiempo de seis golpes: misma velocidad, distinto sticking.",
         "Los acentos cambian de lugar según el tiempo: mirá la tira de sticking.",
         "Pista de fondo propia sobre el seisillo."]),
    "Six stroke + paradiddle-diddle": (
        "Todo en seisillos: los mismos seis golpes con los dobles en dos lugares distintos, un tiempo cada uno.",
        ["Redoble de seis (dobles en el medio) y paradiddle-diddle (dobles al final): la diferencia es solo dónde caen los dobles.",
         "Los acentos: primero y último en el redoble, solo el primero en el paradiddle-diddle.",
         "Pista de fondo propia sobre el seisillo."]),
    "Doubles 16ths": (
        "Dobles en semicorcheas: derecha derecha izquierda izquierda.",
        ["El segundo golpe de cada doble es el difícil: tiene que sonar igual que el primero, a tiempo.",
         "Acento en el tiempo (el primer golpe de la derecha), lo demás suave."]),
    "Accent on 1": (
        "Semicorcheas con acento en el tiempo y taps en el medio.",
        ["Uno fuerte, tres suaves: el contraste tiene que ser claro (se mide como relación de velocidades).",
         "Los taps van bajos, la punta a un par de centímetros del parche."]),
    "Accent on e": (
        "Semicorcheas con el acento en el e (la segunda semicorchea).",
        ["El acento se corre al segundo golpe: cae en la izquierda.",
         "El golpe del tiempo es un tap: cuesta no acentuarlo por reflejo."]),
    "Accent on &": (
        "Semicorcheas con el acento en el &.",
        ["El acento en la tercera semicorchea, con la derecha, a contratiempo.",
         "El tiempo sigue siendo suave: lo marca el metrónomo, no vos."]),
    "Accent on a": (
        "Semicorcheas con el acento en el a (la última).",
        ["El acento cae en la izquierda, justo antes del tiempo siguiente.",
         "El acento del a empuja hacia el tiempo: que no se adelante."]),
    "Moving accent": (
        "El acento camina: en el 1, después en el e, en el &, en el a, uno por tiempo.",
        ["Los cuatro niveles anteriores en un compás: el acento se corre una semicorchea en cada tiempo.",
         "Alterna de mano: derecha, izquierda, derecha, izquierda."]),
    "Double kick 8ths": (
        "Doble bombo en corcheas: derecho, izquierdo. Hi-hat en el tiempo, redoblante en 2 y 4.",
        ["Los pies alternan en cada corchea: el derecho en el tiempo, el izquierdo en el &.",
         "El redoblante del 2 y el 4 cae con un bombo derecho: manos y pies juntos."]),
    "Double kick bursts of two": (
        "Ráfagas de dos: dos semicorcheas en cada tiempo, derecho e izquierdo, y descanso.",
        ["Derecho izquierdo y silencio: el segundo golpe es una semicorchea después, no una corchea.",
         "Arrancar y parar limpio: el pie izquierdo no tiene que colgar un tercer golpe."]),
    "Double kick pairs, left first": (
        "El par del galope solo, con el izquierdo en el tiempo: izquierdo, derecho, y descanso. El derecho espera su semicorchea entera.",
        ["El izquierdo cae con el hi-hat; el derecho llega una semicorchea después, ni antes: la guitarra machaca las dos.",
         "El error típico: el derecho vuelve apurado detrás del izquierdo y el par se cierra 30 ms antes. Escuchá el hueco entre los dos golpes.",
         "Cuando el par entra parejo, el galope es este par corrido al & y al a."]),
    "Double kick gallop tail": (
        "La cola del galope: las dos semicorcheas solas, izquierdo en el &, derecho en el a, y en el tiempo solo el hi-hat.",
        ["El par flota entre los tiempos: el derecho cae justo antes del hi-hat, no encima.",
         "Si el derecho se apura, queda un hueco largo hasta el tiempo: ese hueco es lo que hay que sentir.",
         "El galope completo es esto más el bombo derecho en el tiempo."]),
    "Double kick gallop, feet only": (
        "El galope solo con los pies: derecho, izquierdo, derecho en cada tiempo, y las manos quietas. Toda la atención en el hueco después del primer golpe.",
        ["El derecho toca el tiempo y se queda: el izquierdo entra recién en el &, una corchea entera después, no antes.",
         "Es lo que se te adelanta en el galope completo: el par izquierdo-derecho se cierra hacia el tiempo. Acá la guitarra machaca los tres golpes; dejá que ella marque el hueco.",
         "Cuando salga parejo sin manos, el galope completo es agregarlas en el tiempo."]),
    "Double kick gallop": (
        "El galope con doble pedal: corchea, semicorchea, semicorchea en cada tiempo, derecho, izquierdo, derecho.",
        ["Tres golpes por tiempo con el hueco después del primero: derecho... izquierdo derecho.",
         "El derecho toca dos de los tres: el izquierdo se mete en el medio."]),
    "Double kick 16ths": (
        "Semicorcheas con los pies, derecho en el tiempo, bajo el beat común.",
        ["Dieciséis bombos por compás: parejos, sin acentuar el tiempo con el pie.",
         "Las manos hacen el beat de siempre por encima: independencia entre pies y manos."]),
    "Double kick 16ths, left lead": (
        "La misma corrida arrancando con el pie izquierdo: el pie débil cae en el tiempo.",
        ["Todo igual al anterior con los pies invertidos: el izquierdo en los tiempos, el derecho en los e y a.",
         "El redoblante del 2 y el 4 ahora cae con el pie izquierdo."]),
    "Double kick 16ths, hats 8ths": (
        "Semicorcheas con los pies y el hi-hat en corcheas: manos y pies a distinta velocidad.",
        ["El hi-hat cae cada dos bombos: siempre con el derecho.",
         "Si el hi-hat se acelera a la velocidad de los pies, la mano se contagió: la mano a la mitad."]),
    "Double kick triplets": (
        "Tresillos de corchea con los pies, alternando: el pie que empieza cambia en cada tiempo.",
        ["Tres golpes por tiempo: el derecho empieza el 1, el izquierdo el 2, y así.",
         "El hi-hat y el redoblante caen a veces con el derecho y a veces con el izquierdo."]),
    "Double kick, bursts of four": (
        "Ráfagas de cuatro: cuatro semicorcheas en el 2 y el 4, silencio en el 1 y el 3. Arrancar y parar limpio.",
        ["El silencio es parte del ejercicio: nada en el 1 ni en el 3.",
         "Las cuatro semicorcheas empiezan con el redoblante: mano y pie derecho juntos en el 2 y el 4."]),
    "Tight and open": (
        "Corcheas en el cuerpo del hi-hat: un compás con el pedal apretado (cerrado), un compás con el pedal levantado (abierto). Sentí el recorrido del pedal.",
        ["Se juzga la apertura: el pedal a fondo para el cerrado, bien levantado para el abierto. La posición del pedal es lo que se mide, no el sonido.",
         "El cambio es en el 1 de cada compás: el pie se mueve entre el último golpe de uno y el primero del otro."]),
    "Half open": (
        "Las mismas corcheas a medio pedal: el hi-hat medio abierto, chorreado. Encontrá el punto y sostenelo.",
        ["Medio pedal en este pedal se lee entre 8 y 28 de cerrado: es más abierto de lo que parece.",
         "Sostener la posición es el ejercicio: si el pie se cansa y baja, el golpe se lee cerrado."]),
    "Openness ladder": (
        "La escalera de apertura: dos compases cerrado, dos medio, dos abierto, y de vuelta hasta cerrado. Borde en los tiempos y cuerpo en los &.",
        ["Tres posiciones de pedal, cada una sostenida dos compases: la escalera sube y baja.",
         "La zona también se juzga: borde (el filo de la baqueta) en el tiempo, cuerpo (la punta) en el &."]),
    "Open on the &": (
        "Corcheas cerradas, el hi-hat se abre en el & de 4 y el pie lo cierra en el 1 siguiente: el hi-hat disco.",
        ["El golpe abierto es uno solo por compás, en el & de 4: el pie sube justo antes y baja en el 1.",
         "El chick del 1 (el pedal cerrándose) es una nota del nivel: se juzga como golpe."]),
    "Bark": (
        "El ladrido: un hi-hat abierto ahogado enseguida por el pie. Abierto en el & de 2, chick en el 3, cerrado alrededor.",
        ["Abrir y cerrar en una corchea: el pie sube para el & y baja de golpe en el 3.",
         "El chick del 3 tiene que sonar seco: es el que corta el platillo."]),
    "Foot on 2 and 4": (
        "Sin baquetas en el hi-hat: el pie hace chick en 2 y 4 bajo bombo y redoblante, y después en cada tiempo.",
        ["El chick es el pedal bajando fuerte: se mide la velocidad de la pisada.",
         "Pie izquierdo y mano en el mismo golpe (2 y 4): que caigan juntos."]),
    "Bow and edge": (
        "Corcheas cerradas alternando la punta en el cuerpo y el filo en el borde: el hombro de la baqueta habla en el tiempo.",
        ["Borde en el tiempo (acento, con el filo), cuerpo en el & (tap, con la punta).",
         "Es el mismo ángulo de la baqueta cambiando: la mano gira, no se mueve de lugar."]),
    "Edge on the open": (
        "Hi-hats abiertos en el borde: el & de 2 y el & de 4 abiertos y en el borde, los chicks los cierran en el 3 y en el 1.",
        ["Dos aperturas por compás, las dos en el borde: pie arriba y filo de la baqueta a la vez.",
         "Cada apertura tiene su chick: el 3 y el 1 son notas del pie."]),
    "Sixteenths, mid and tight": (
        "Semicorcheas en el cuerpo, cerrado en los tiempos y medio abierto en el medio: el pedal respira con cada tiempo.",
        ["El pie baja a fondo en cada tiempo y se levanta a medio entre tiempos: cuatro respiraciones por compás.",
         "Las semicorcheas siguen parejas mientras el pie se mueve: la mano no sabe lo que hace el pie."]),
    "Hi-hat song": (
        "Dieciséis compases que usan todo: groove cerrado, &s abiertos, acentos en el borde, una sección a medio pedal, ladridos y el pie en 2 y 4.",
        ["Todos los niveles de hi-hat en una pieza: cada sección de cuatro compases cambia la técnica.",
         "Si una sección se cae, ese es el nivel para volver."]),

    # --- Pop punk -------------------------------------------------------------------------
    "1 · Driving eighths": (
        "El pulso del pop punk: hi-hat en cada corchea, bombo en 1 y 3, redoblante en 2 y 4, un crash abriendo cada cuatro compases. Rápido y parejo.",
        ["A 140 bpm la corchea va rápido: la muñeca relajada y el golpe corto.",
         "El crash del 1 cada cuatro compases: la mano sale del hi-hat y vuelve en la corchea siguiente.",
         "Con ] el tempo sube hacia los 160..190 del estilo cuando las manos estén."]),
    "2 · Four on the floor": (
        "El bombo pasa a cada tiempo bajo el mismo hi-hat y el mismo backbeat: el pisotón punk.",
        ["Bombo en los cuatro tiempos, parejo: no más fuerte en el 1 y el 3.",
         "Redoblante y bombo juntos en el 2 y el 4."]),
    "3 · The push": (
        "El push: bombo en 1, en el & de 2 y en 3; cada segundo compás agrega el & de 4, empujando hacia el compás siguiente. El bombo del pop punk.",
        ["El bombo del & de 2 cae entre el 2 y el 3, con un hi-hat: es el que da el impulso.",
         "El & de 4 aparece compás por medio: la anticipación al 1 siguiente."]),
    "4 · Tight and open": (
        "Cuatro compases de estrofa con el hi-hat cerrado, cuatro de estribillo con el hi-hat abierto y el pie fuera del pedal; un crash en el cambio, los pushes siguen.",
        ["Se juzga la apertura: cerrado a fondo en la estrofa, bien abierto en el estribillo.",
         "El cambio de apertura es en el crash: el pie se levanta en el mismo golpe.",
         "El bajo y los acordes abiertos de la guitarra tocan la figura del bombo, compás por compás: escuchala en la música antes de buscarla con el pie."]),
    "5 · Washing the crash": (
        "El estribillo lava el crash izquierdo en cada corchea en lugar del hi-hat; el crash derecho marca la vuelta a la estrofa y la mano vuelve al hi-hat en el 2, con el redoblante.",
        ["Corcheas en el crash: livianas, es un colchón de platillo, no ocho acentos.",
         "Dieciséis compases: estrofa, estribillo, y el crash derecho avisa la vuelta.",
         "Después del crash del 1 no hay hi-hat en el & de 1: el platillo suena hasta el backbeat y la mano llega al hi-hat junto con el redoblante.",
         "El bajo y la guitarra tocan la figura del bombo, compás por compás, como en el nivel 4: el push está en la música.",
         "El mismo nivel existe lavando el crash derecho: los toms o las flechas en la lista eligen el lado."]),
    "6 · Snare on the &": (
        "El skank: bombo en cada tiempo, redoblante en cada &, hi-hat acompañando. La estrofa hace el skank, el estribillo lava el crash con los pushes.",
        ["El redoblante a contratiempo, nunca en el tiempo: si lo sentís dado vuelta, parás y contás.",
         "El estribillo vuelve al backbeat normal: dos grooves distintos en el mismo nivel."]),
    # The fill drills: one fill each, two bars looping, before the level that plays them all.
    "7 · Drill: two snares on 4": (
        "El primer relleno solo, compás por medio: el hi-hat corta después del & de 3, caen dos redoblantes en las "
        "corcheas del 4 y el crash del 1 lo cierra. Ocho veces seguidas, para que la mano lo sepa de memoria antes "
        "de que el nivel lo use.",
        ["Son exactamente los dos compases del nivel «9 · Eighth-note fills»: lo que aprendas acá entra ahí tal cual.",
         "Cada frase del transporte (1..8) es una celda de dos compases: l11 repite una sola hasta que salga.",
         "Las dos corcheas del 4 son derecha e izquierda, parejas: no las apures para llegar al crash."]),
    "8 · Drill: down the kit": (
        "El relleno que baja por el set, compás por medio: redoblante en el 3 y en su &, tom en el 4, chancha en el "
        "& de 4, y el crash en el 1.",
        ["El hi-hat corta en el 3: el relleno empieza un tiempo antes que el del nivel anterior.",
         "Tres tambores en cuatro corcheas: la vista va adelante de la mano, primero al tom y después a la chancha.",
         "Con los dos drills hechos, el nivel que sigue es alternarlos."]),
    "9 · Eighth-note fills": (
        "Cada cuarto compás termina en un relleno en corcheas: dos redoblantes en el 4; redoblante, tom y chancha bajando sobre 3 y 4; el crash cae en el 1 que sigue.",
        ["A esta velocidad las corcheas del relleno van como semicorcheas de un nivel lento: parejas.",
         "El crash del 1 cierra cada relleno: apuntá a ese golpe."]),
    "10 · Drill: four snares on 4": (
        "Las semicorcheas solas: cuatro redoblantes en el tiempo 4, el hi-hat cortando después del & de 3 y el crash "
        "en el 1. Compás por medio, ocho veces.",
        ["Alternadas, derecha izquierda derecha izquierda, de la muñeca y sin apretar la baqueta.",
         "Si no salen parejas a 148, bajá con [ y subí de a 0,05x cuando salgan.",
         "Es el primero de los cuatro rellenos de semicorcheas: uno por nivel y después todos juntos."]),
    "11 · Drill: snare and rack pairs": (
        "Las mismas cuatro semicorcheas repartidas en dos tambores: dos redoblantes en el 4 y dos toms en su &.",
        ["El cambio de tambor cae a mitad del tiempo: las cuatro notas tienen que sonar tan parejas como en el redoblante solo.",
         "Derecha e izquierda en el redoblante, derecha e izquierda en el tom: el brazo se mueve, la mano no cambia."]),
    "12 · Drill: the walk down": (
        "Ocho semicorcheas seguidas sobre los tiempos 3 y 4: cuatro redoblantes, dos toms, dos chanchas.",
        ["Empieza en el 3: el hi-hat corta un tiempo antes que en los dos rellenos anteriores.",
         "Dos tambores nuevos en la última mitad: bajá con la vista mientras las manos siguen alternando."]),
    "13 · Drill: the whole kit": (
        "Tres tiempos de relleno: redoblante en el 2, tom en el 3, chancha en el 4, cuatro semicorcheas en cada uno.",
        ["El relleno más largo del curso: el hi-hat corta en el & de 1 y hay que contar para llegar al crash.",
         "Cuatro golpes por tambor, el cambio siempre en el tiempo: si se corre uno, se corre todo el compás."]),
    "14 · Sixteenth fills": (
        "Los rellenos pasan a semicorcheas: cuatro redoblantes en el 4; redoblante y tom en pares; la bajada redoblante, tom, chancha sobre 3 y 4; todo el set desde el 2.",
        ["Semicorcheas a 148 bpm: alternadas, de la muñeca, sin apretar la baqueta.",
         "El último relleno arranca en el 2: tres tiempos de relleno, contá para llegar al 1."]),
    "15 · Crash on the &": (
        "El crash derecho cae con el bombo en el & de 4 de los compases 2 y 4, antes de la línea del compás; el crash izquierdo responde en el 1. Los rellenos siguen.",
        ["Crash y bombo juntos en el & de 4: es una anticipación, y el 1 que sigue lleva el otro crash.",
         "Dos crashes seguidos, derecho e izquierdo, separados por una corchea."]),
    "16 · Stabs": (
        "Stop time: la banda pega en el 1 y en el & de 2 y la batería pega con ella, bombo y los dos crashes juntos, nada en el medio; una corrida de redoblante en el 4 trae el groove de vuelta.",
        ["Los stabs son silencios con golpes: lo difícil es no tocar entre ellos.",
         "Bombo y dos crashes a la vez: los tres exactamente juntos.",
         "La corrida del 4 es la vuelta al groove: cuatro semicorcheas y el 1."]),
    "17 · Half-time verse": (
        "La estrofa va en medio tiempo, redoblante en 3 y bombo en 1 y en el & de 2; el estribillo vuelve al backbeat sobre el crash, con el crash derecho en el & de 4.",
        ["Dos velocidades de sensación en el mismo tempo: el hi-hat no cambia, cambia dónde cae el redoblante.",
         "El estribillo tiene los crashes del &: la anticipación de pop punk."]),
    "18 · The build": (
        "Estrofa en medio tiempo, después el pre-estribillo: corcheas en el redoblante creciendo de fantasmas a acentos sobre el bombo en los tiempos, semicorcheas en el último compás; el estribillo cae en el crash.",
        ["El build es dinámico: cada compás más fuerte que el anterior, de casi nada a todo.",
         "El último compás del build va a semicorcheas: el doble de golpes, mismo crescendo."]),
    "19 · Doubles under the push": (
        "Dobles de bombo: dos bombos seguidos en semicorcheas, el & a de 1 y de 3, un solo pedal, bajo el hi-hat; los pushes y los rellenos en semicorcheas vuelven.",
        ["A 150 bpm el doble sale del rebote: talón-punta o dos golpes de punta, sin levantar la pierna dos veces.",
         "Diecisiete compases con rellenos: el doble no puede desarmar las manos."]),
    "20 · Ride bridge": (
        "El puente lleva la mano derecha al ride sobre los dobles de bombo, con un tom en el & de 4 compás por medio; el estribillo vuelve al crash.",
        ["Sin hi-hat en este nivel: el ride en las corcheas y el crash en el estribillo.",
         "El tom del & de 4 es una sola nota: la mano izquierda va sola mientras la derecha sigue en el ride."]),
    "21 · Drill: around the kit": (
        "Un compás entero de relleno compás por medio, sobre el groove de dobles de bombo: semicorcheas simples, "
        "redoblante en el 1, tom en el 2, chancha en el 3 y en el 4.",
        ["Ocho semicorcheas seguidas en la chancha al final: son las que se desarman, mantené la altura de la baqueta.",
         "El compás de groove lleva los dobles de bombo: el pie sigue trabajando entre relleno y relleno."]),
    "22 · Drill: toms on the eighths": (
        "El relleno en corcheas: un tom con el bombo en cada tiempo, tom en 1 y 2, chancha en 3 y 4, y dos "
        "redoblantes entre medio de cada par.",
        ["El bombo cae junto con el tom en los cuatro tiempos: pie y mano exactamente juntos.",
         "Los dos redoblantes van entre los tiempos: no son un adorno, son el pulso de semicorcheas."]),
    "23 · Drill: kick and snare pairs": (
        "Pies y manos alternando pares de semicorcheas todo el compás: dos bombos, dos redoblantes, cuatro veces.",
        ["Dos bombos seguidos en semicorcheas: es el mismo doble del groove, ahora en cada tiempo.",
         "El par de redoblante entra apenas termina el del bombo: sin hueco y sin pisarse."]),
    "24 · Drill: crashes to close": (
        "El relleno que cierra una canción: redoblante, tom y chancha sobre 1, 2 y 3, y los dos crashes con el bombo "
        "en el 4.",
        ["Los dos crashes y el bombo son un solo golpe: los tres juntos en el 4.",
         "El compás de groove de este nivel abre sin crash, para que los dos del 4 sigan sonando sobre el 1: no lo llenes."]),
    "25 · Whole-bar fills": (
        "Rellenos de un compás entero cada cuatro: semicorcheas simples redoblante, tom, chancha, chancha; toms en corcheas con pares de redoblante entre medio; bombo y redoblante en pares; los dos crashes en el 4 para cerrar.",
        ["Cuatro rellenos distintos de un compás: mirá el carril de los toms para saber cuál viene.",
         "El último termina con los dos crashes en el 4 y el 1 vacío: no lo llenes."]),
    "26 · Pop punk anthem": (
        "Treinta y dos compases con todo: una intro con skank y stabs, una estrofa con los pushes y rellenos, el build, un estribillo lavando el crash con los crashes del &, un puente en medio tiempo sobre el ride, el último estribillo, los dos crashes para cerrar.",
        ["Todo el curso en una canción: cada sección de cuatro u ocho compases es uno de los niveles anteriores.",
         "Ocho secciones: usá las frases del transporte (1..0) para practicar la que se caiga.",
         "Es largo: la resistencia es parte del nivel, relajá entre secciones."]),
}

# the structural facts, in Spanish
_SUB = {1: "negras", 2: "corcheas", 3: "tresillos", 4: "semicorcheas", 6: "seisillos"}
_INST = {"kick": "bombo", "snare": "redoblante", "hihat": "hi-hat", "pedal": "pedal del hi-hat", "crash": "crash izquierdo",
         "crash2": "crash derecho", "tom1": "tom", "floor": "chancha", "ride": "ride"}
_BACKING = {"cumbia": "una cumbia", "keygen": "el tema de la cortina", "punk": "una banda de pop punk",
            "kick": "un metal que machaca la figura del bombo"}
_SWAP = {"derecha": "izquierda", "izquierda": "derecha", "derecho": "izquierdo", "izquierdo": "derecho",
         "R": "L", "L": "R"}


def _swap_hands(text):
    return re.sub(r"\b(derecha|izquierda|derecho|izquierdo|R|L)\b", lambda m: _SWAP[m.group(0)], text)


def facts(chart):
    """One line of facts from the chart itself: tempo, compases, subdivisión, cuerpos, qué se juzga."""
    subs = sorted({s for _, s in chart.segments}) if chart.segments else []
    parts = [f"{chart.bpm:.0f} bpm", f"{chart.bars} compases", f"{len(chart.notes)} notas"]
    if subs:
        parts.append(" y ".join(_SUB.get(s, f"{s} por tiempo") for s in subs))
    insts = []
    seen = set()
    for n in chart.notes:
        if n.key not in seen:
            seen.add(n.key)
            insts.append(_INST.get(n.key, n.key))
    parts.append(", ".join(insts))
    return "  ·  ".join(parts)


def judged(chart):
    """What the level judges besides the timing, as short Spanish phrases."""
    out = []
    if chart.dynamics:
        out.append("se juzgan las dinámicas: acentos fuertes, taps suaves")
    if chart.expression:
        out.append("se juzga la articulación del hi-hat: apertura, zona y chick")
    if chart.lead and chart.mirror == "crash":
        out.append("el wash en el crash " + ("derecho" if chart.lead == "R" else "izquierdo") + " (los toms o las flechas en la lista lo cambian)")
    elif chart.lead:
        out.append("mano guía " + ("derecha" if chart.lead == "R" else "izquierda") + " (los toms o las flechas en la lista la cambian)")
    if chart.backing:
        out.append("pista de fondo: " + _BACKING.get(chart.backing, chart.backing))
    return out


def intro_for(chart, lang="es"):
    """(learn, notes, facts, judged) for a level: what it teaches, what to watch, its facts
    and what it judges. With lang "en" the English description stands in for the text."""
    entry = INTROS.get(chart.name)
    if lang != "es" or entry is None:
        learn, notes = chart.desc, []
    else:
        learn, notes = entry
        if chart.lead and chart.lead != chart.home:        # the other hand leads, or the other crash is washed
            learn, notes = _swap_hands(learn), [_swap_hands(n) for n in notes]
    return learn, list(notes), facts(chart), judged(chart)


def every_level():
    from .chart import BEATS, EXERCISES, COURSES
    return list(BEATS) + list(EXERCISES) + [ch for c in COURSES for ch in c.levels]


def missing():
    """Level names without an intro (a test keeps this empty)."""
    return [ch.name for ch in every_level() if ch.name not in INTROS]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="bathero.intro", description="print the level intros")
    ap.add_argument("name", nargs="*", help="level names (default: every level)")
    ap.add_argument("--lang", default="es")
    args = ap.parse_args(argv)
    for ch in every_level():
        if args.name and ch.name not in args.name:
            continue
        learn, notes, fx, jd = intro_for(ch, args.lang)
        print(f"== {ch.name}\n   {fx}\n   {learn}")
        for n in notes:
            print(f"   · {n}")
        for j in jd:
            print(f"   ~ {j}")
    if missing():
        print("SIN INTRO:", ", ".join(missing()))


if __name__ == "__main__":
    main()

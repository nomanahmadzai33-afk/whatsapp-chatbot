"""Approved PuroCuento launch knowledge.

Every item is editable from the dashboard.  Only records with status=approved
are supplied to the language model.
"""

SEED_KNOWLEDGE = [
    {
        "id": "company-contact",
        "title": "Empresa, contacto y horario",
        "category": "Empresa",
        "source_url": "https://purocuento.es/",
        "content": (
            "PuroCuento es una empresa de Madrid especializada en servicios y material para "
            "producciones audiovisuales, rodajes y eventos en España. Contacto operativo: "
            "operativa@purocuento.es y +34 657 654 417. Horario de atención: lunes a viernes "
            "de 09:00 a 14:00 y de 16:00 a 18:00, hora de Madrid; fines de semana cerrado."
        ),
    },
    {
        "id": "quotes-policy",
        "title": "Presupuestos, precios y disponibilidad",
        "category": "Política comercial",
        "source_url": "https://purocuento.es/",
        "content": (
            "PuroCuento trabaja con propuestas personalizadas. El asistente nunca comunica precios, "
            "estimaciones, descuentos, stock o disponibilidad ni confirma reservas. Recopila el briefing "
            "y lo transfiere al equipo, que valida alcance, viabilidad, disponibilidad y presupuesto."
        ),
    },
    {
        "id": "green-room",
        "title": "Green Rooms y camerinos",
        "category": "Green Room",
        "source_url": "https://purocuento.es/green-room/",
        "content": (
            "Diseño y montaje de Green Rooms, camerinos y zonas para cliente, agencia o talento. "
            "El planteamiento puede integrar lounge, áreas de trabajo, maquillaje y vestuario, espejos, "
            "percheros, video village, privacidad, climatización, iluminación, conectividad, carga, "
            "hospitality y entretenimiento. La solución depende del plano, accesos, usuarios, rider, "
            "duración y condiciones de la localización."
        ),
    },
    {
        "id": "backstage-hospitality",
        "title": "Backstage, Hospitality y VIP Care",
        "category": "Hospitality",
        "source_url": "https://purocuento.es/",
        "content": (
            "PuroCuento prepara backstage, hospitality, VIP Care y craft service para equipos, invitados "
            "y talento. La planificación profesional considera privacidad, recorridos, control de accesos, "
            "descanso, catering, baños, seguridad, limpieza y coordinación con producción."
        ),
    },
    {
        "id": "production-spaces",
        "title": "Espacios y oficinas de producción",
        "category": "Producción",
        "source_url": "https://purocuento.es/",
        "content": (
            "Alquiler y acondicionamiento de espacios, oficinas de producción, zonas de coordinación y "
            "check-in. Pueden equiparse con mobiliario de trabajo, conectividad, impresión, carga, "
            "comunicaciones, climatización, señalética e iluminación funcional."
        ),
    },
    {
        "id": "set-support",
        "title": "Set Support y Unit Manager",
        "category": "Producción",
        "source_url": "https://purocuento.es/",
        "content": (
            "Servicios de apoyo en set y Unit Manager para coordinar necesidades operativas de rodajes y "
            "eventos: implantación, logística, proveedores, circulación, consumibles, limpieza, seguridad "
            "y respuesta a necesidades durante la jornada."
        ),
    },
    {
        "id": "video-village",
        "title": "Production Van, Video Van y video village",
        "category": "Audiovisual",
        "source_url": "https://purocuento.es/",
        "content": (
            "Soluciones de Production Van, Video Van y video village para seguimiento y coordinación de "
            "rodajes. La configuración final de monitores, señal, comunicaciones, energía y ubicación se "
            "define tras revisar el flujo técnico, el espacio y el equipo de producción."
        ),
    },
    {
        "id": "video-van-verified-details",
        "title": "Video Van: capacidad, equipamiento y límites",
        "category": "Audiovisual",
        "source_url": "https://purocuento.es/video-van/",
        "content": (
            "La Video Van publicada por PuroCuento es un Video Village móvil premium para hasta ocho "
            "personas. Integra dos monitores profesionales Sony Trimaster EL de 25 pulgadas, blackout, "
            "climatización independiente, iluminación regulable, wifi, nevera, cafetera, conexiones y "
            "generador propio. Admite señal por cable, antena, Teradek, QTAKE 4G/5G o streaming y puede "
            "trabajar con múltiples señales. El técnico de video assist no está incluido y lo asigna la "
            "productora. No se utiliza como vehículo de transporte de material o catering. Precios, "
            "disponibilidad y configuraciones finales siempre deben validarse con Operativa."
        ),
    },
    {
        "id": "temporary-spaces",
        "title": "BlackWall, EasyDrape, carpas y espacios temporales",
        "category": "Espacios temporales",
        "source_url": "https://purocuento.es/",
        "content": (
            "Creación y división de espacios temporales mediante BlackWall, EasyDrape o Pipe & Drape, "
            "carpas, cerramientos y enmoquetado. La propuesta debe considerar medidas, altura, superficie, "
            "interior o exterior, viento, accesos, seguridad, privacidad y tiempos de montaje y desmontaje."
        ),
    },
    {
        "id": "furniture-decor",
        "title": "Mobiliario, textiles y decoración",
        "category": "Equipamiento",
        "source_url": "https://purocuento.es/",
        "content": (
            "Alquiler e implantación de mesas, sillas, sofás, textiles, alfombras, mobiliario de trabajo, "
            "elementos de almacenaje y decoración. La selección se realiza según uso, aforo, imagen del "
            "proyecto, dimensiones y necesidades de transporte y montaje."
        ),
    },
    {
        "id": "makeup-wardrobe",
        "title": "Maquillaje y vestuario",
        "category": "Talento",
        "source_url": "https://purocuento.es/",
        "content": (
            "Acondicionamiento de áreas de maquillaje, peluquería y vestuario con espejos, iluminación, "
            "mesas, sillas, percheros, separación visual, electricidad y organización del flujo de talento."
        ),
    },
    {
        "id": "climate-energy",
        "title": "Climatización, energía e iluminación",
        "category": "Infraestructura",
        "source_url": "https://purocuento.es/",
        "content": (
            "Soluciones de climatización, ventilación, distribución eléctrica, estaciones de energía, "
            "puntos de carga e iluminación funcional o ambiental. La potencia y configuración se validan "
            "técnicamente según equipos, superficie, condiciones del espacio y duración."
        ),
    },
    {
        "id": "communications",
        "title": "Comunicaciones y conectividad",
        "category": "Infraestructura",
        "source_url": "https://purocuento.es/",
        "content": (
            "Conectividad y comunicaciones para producción: redes y Wi-Fi, walkies, puntos de carga y "
            "coordinación entre áreas. Cobertura, capacidad y equipos se revisan según localización, "
            "usuarios y operativa."
        ),
    },
    {
        "id": "transport-installation",
        "title": "Transporte, montaje y desmontaje",
        "category": "Logística",
        "source_url": "https://purocuento.es/",
        "content": (
            "PuroCuento puede coordinar transporte, carga y descarga, montaje, asistencia durante el "
            "servicio y desmontaje. Para planificar se necesitan ubicación, accesos, horarios, planta o "
            "muelle de carga, restricciones del recinto, fechas y volumen aproximado."
        ),
    },
    {
        "id": "rental-process-verified",
        "title": "Proceso verificado de alquiler y preparación",
        "category": "Proceso",
        "source_url": "https://purocuento.es/alquiler-de-material/",
        "content": (
            "PuroCuento ofrece solo alquiler con recogida y devolución en sus instalaciones, alquiler "
            "con transporte con o sin montaje, y asesoramiento o producción integral. El material sale "
            "revisado y probado. El soporte técnico durante el rodaje no está incluido automáticamente, "
            "pero puede contratarse según disponibilidad. La solicitud inicial no confirma material: "
            "Operativa revisa el pedido, emite el presupuesto definitivo, confirma disponibilidad o "
            "propone alternativas y solo después de la aprobación confirma y reserva el proyecto."
        ),
    },
    {
        "id": "rental-categories-verified",
        "title": "Categorías publicadas de material de producción",
        "category": "Equipamiento",
        "source_url": "https://purocuento.es/alquiler-de-material/",
        "content": (
            "Las categorías publicadas incluyen climatización; mesas, sillas y mobiliario auxiliar; "
            "separadores, carpas, BlackWall y EasyDrape; vestuario y maquillaje; protección y seguridad; "
            "oficina de producción y papelería; limpieza y reciclaje; FX y efectos especiales; catering "
            "y PuroCoffee; señalización de tráfico y car care; electricidad, iluminación y equipamiento "
            "audiovisual; y consumibles de producción. Para recomendar una solución hay que conocer uso, "
            "aforo, espacio, fecha, localización, accesos y si se necesita transporte o montaje."
        ),
    },
    {
        "id": "technical-visit",
        "title": "Visita técnica y proceso de proyecto",
        "category": "Proceso",
        "source_url": "https://purocuento.es/",
        "content": (
            "En proyectos complejos se recomienda una visita técnica para revisar medidas, accesos, "
            "circulaciones, energía, climatización, seguridad y logística. El asistente puede preparar un "
            "briefing preliminar; el equipo humano revisa y aprueba la solución técnica y comercial final."
        ),
    },
    {
        "id": "speakers",
        "title": "Altavoces y sonido publicados",
        "category": "Audiovisual",
        "source_url": "https://purocuento.es/",
        "content": (
            "Solo cuando el cliente pregunte expresamente por altavoces o sonido pueden mencionarse como "
            "orientación los modelos publicados: JBL Charge 6, Marshall Acton III, Tribit StormBox Blast, "
            "JBL PartyBox Stage 320, Alto Professional TS115W y JBL PartyBox 720. Nunca se confirma stock, "
            "precio o idoneidad final sin conocer aforo, uso, corriente, micrófonos, interior/exterior y ruido."
        ),
    },
]
